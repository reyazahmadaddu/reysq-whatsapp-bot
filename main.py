import os
from openai import OpenAI
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from tinydb import TinyDB, Query
import uvicorn
import httpx
import aiofiles
from deep_translator import GoogleTranslator

# Load environment variables
load_dotenv()

app = FastAPI()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

db = TinyDB("memory.json")
UserMemory = Query()

SYSTEM_PROMPT = {
    "role": "system",
    "content": """
You are ReysQ — a warm, emotionally intelligent AI health companion, like a friendly pocket doctor who remembers how the user has been feeling.

You receive a summary of the last 8 messages (excluding the most recent one). Treat it as memory.

You are trained in medical triage.
Your goal is to listen carefully, ask relevant follow-up questions, and provide safe, step-by-step suggestions for symptom relief.
Speak with empathy, emotional support, and clarity — not as a robotic assistant.
Keep your tone conversational and reassuring, as if you're personally guiding the user through their symptoms.
Avoid medical jargon unless necessary. If symptoms are serious, advise calmly to consult a real doctor.
If symptoms are mild, give a 2–3 day care plan, track symptoms, and offer to follow up.
Always close with a positive, human touch. You are their pocket doctor, not a disclaimer generator.
Keep replies short enough to be sent via WhatsApp.
"""
}

@app.get("/")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"status": "unauthorized"}

async def summarize_messages(messages: List[Dict]) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": "Summarize this conversation in a short clinical memory, preserving key symptoms, plans, and tone."},
                *messages
            ],
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return "Summary failed. Memory cleared."

async def text_to_speech(text: str) -> str:
    try:
        speech_response = client.audio.speech.create(
            model="tts-1",
            voice="shimmer",  # Female-sounding voice
            input=text
        )
        audio_path = "output.ogg"
        with open(audio_path, "wb") as f:
            f.write(speech_response.content)
        return audio_path
    except Exception as e:
        print("TTS error:", e)
        return ""

async def upload_audio_to_whatsapp(file_path: str):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }
    data = {
        "messaging_product": "whatsapp",
        "type": "audio/ogg"
    }

    async with httpx.AsyncClient() as client_http:
        async with aiofiles.open(file_path, "rb") as f:
            file_data = await f.read()

        files = {
            "file": ("voice.ogg", file_data, "audio/ogg")
        }

        response = await client_http.post(url, headers=headers, data=data, files=files)
        response.raise_for_status()
        return response.json()["id"]

async def send_audio_reply(user_id: str, media_id: str):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": user_id,
        "type": "audio",
        "audio": {"id": media_id}
    }

    async with httpx.AsyncClient() as client_http:
        await client_http.post(url, headers=headers, json=payload)

def transliterate_to_hindi(text: str) -> str:
    try:
        return GoogleTranslator(source="auto", target="hi").translate(text)
    except Exception:
        return text

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]["value"]
        messages = changes.get("messages")
        if not messages:
            print("No message in update.")
            return {"status": "no message"}

        msg = messages[0]
        user_id = msg["from"]
        user_text = msg.get("text", {}).get("body")

        if not user_text:
            print("No text in message")
            return {"status": "no text"}

        # Detect if user is typing Roman Hindi and transliterate
        roman_hindi = transliterate_to_hindi(user_text)

        record = db.get(UserMemory.user_id == user_id)
        chat_history = record["messages"] if record else []
        chat_history.append({"role": "user", "content": roman_hindi})

        if len(chat_history) > 8:
            summary = await summarize_messages(chat_history)
            chat_history = [{"role": "assistant", "content": summary}]

        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[SYSTEM_PROMPT] + chat_history,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()

        chat_history.append({"role": "assistant", "content": reply})
        if record:
            db.update({"messages": chat_history}, UserMemory.user_id == user_id)
        else:
            db.insert({"user_id": user_id, "messages": chat_history})

        # Text-to-speech
        audio_file = await text_to_speech(reply)
        if audio_file:
            media_id = await upload_audio_to_whatsapp(audio_file)
            await send_audio_reply(user_id, media_id)
        else:
            # Fallback to text reply
            await send_text_reply(user_id, reply)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

async def send_text_reply(user_id: str, reply: str):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": user_id,
        "type": "text",
        "text": {"body": reply}
    }

    async with httpx.AsyncClient() as client_http:
        await client_http.post(url, headers=headers, json=payload)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
