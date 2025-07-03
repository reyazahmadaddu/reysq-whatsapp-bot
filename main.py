import os
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from tinydb import TinyDB, Query
import uvicorn
import httpx
from openai import OpenAI

# Load environment variables
load_dotenv()

app = FastAPI()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Meta config
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# TinyDB setup
db = TinyDB("memory.json")
UserMemory = Query()

# System prompt
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

# Verification
@app.get("/")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"status": "unauthorized"}

# Summarize last 8 messages
async def summarize_messages(messages: List[Dict]) -> str:
    try:
        summary_response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": "Summarize this conversation in a short clinical memory, preserving key symptoms, mood, advice, and emotional tone."},
                *messages
            ],
            max_tokens=200
        )
        return summary_response.choices[0].message.content.strip()
    except Exception as e:
        return "Summary failed. Memory cleared."

# Text-to-Speech (female voice)
async def generate_voice(text: str, filename: str = "voice_reply.mp3"):
    response = client.audio.speech.create(
        model="tts-1-hd",
        voice="nova",  # Female-sounding voice
        input=text
    )
    with open(filename, "wb") as f:
        f.write(response.content)

# WhatsApp webhook
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]["value"]
        messages = changes.get("messages")
        if not messages:
            return {"status": "no message"}

        msg = messages[0]
        user_text = msg["text"]["body"]
        user_id = msg["from"]

        # Load memory
        record = db.get(UserMemory.user_id == user_id)
        chat_history = record["messages"] if record else []
        chat_history.append({"role": "user", "content": user_text})

        # Summarize if over 8 messages
        if len(chat_history) > 8:
            summary = await summarize_messages(chat_history)
            chat_history = [{"role": "assistant", "content": summary}]

        # Generate reply
        reply_response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[SYSTEM_PROMPT] + chat_history,
            max_tokens=500
        )
        reply_text = reply_response.choices[0].message.content.strip()

        # Save reply
        chat_history.append({"role": "assistant", "content": reply_text})
        if record:
            db.update({"messages": chat_history}, UserMemory.user_id == user_id)
        else:
            db.insert({"user_id": user_id, "messages": chat_history})

        # Generate voice
        await generate_voice(reply_text)
        audio_url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
        headers = {
            "Authorization": f"Bearer {ACCESS_TOKEN}"
        }

        # Upload voice to WhatsApp
        with open("voice_reply.mp3", "rb") as file:
            files = {"file": ("voice_reply.mp3", file, "audio/mpeg")}
            data = {
                "messaging_product": "whatsapp",
                "type": "audio"
            }
            async with httpx.AsyncClient() as client_http:
                audio_upload = await client_http.post(audio_url, headers=headers, data=data, files=files)
                media_id = audio_upload.json().get("id")

        # Send text
        async with httpx.AsyncClient() as client_http:
            await client_http.post(
                f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
                headers={
                    "Authorization": f"Bearer {ACCESS_TOKEN}",
                    "Content-Type": "application/json"
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": user_id,
                    "type": "text",
                    "text": {"body": reply_text}
                }
            )

        # Send audio
        async with httpx.AsyncClient() as client_http:
            await client_http.post(
                f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
                headers={
                    "Authorization": f"Bearer {ACCESS_TOKEN}",
                    "Content-Type": "application/json"
                },
                json={
                    "messaging_product": "whatsapp",
                    "to": user_id,
                    "type": "audio",
                    "audio": {"id": media_id}
                }
            )

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

# Run
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
