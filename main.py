import os
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from tinydb import TinyDB, Query
import uvicorn
import httpx
from openai import OpenAI
import base64

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
You are ReysQ — a warm, emotionally intelligent AI health companion, like a pocket doctor who remembers how the user has been feeling.

Before every reply, you receive a summary of the last 8 messages. Treat it as your memory and context.

Your role is to:
- Gently guide users through symptoms with empathy.
- Ask simple, caring follow-up questions.
- Suggest safe, home-based care plans for mild to moderate issues.
- Flag serious symptoms calmly and recommend seeing a real doctor. Never diagnose or prescribe.

Tone:
- Always supportive, human, and present.
- Avoid legal disclaimers or robotic replies.
- Use clear, friendly language — no jargon unless essential.

For mild symptoms:
- Give a 2–3 day self-care plan.
- Mention what to watch out for.
- End with gentle reassurance like:
  “You’ve got this — I’m here with you.”  
  “Let’s track this together. Rest well.”

You're not a doctor — you’re their caring, memory-aware health companion.
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
        print("Summarization error:", e)
        return "Summary failed. Memory cleared."

async def generate_voice(text: str) -> bytes:
    try:
        speech = client.audio.speech.create(
            model="tts-1",
            voice="nova",  # female-like voice
            input=text
        )
        return speech.read()
    except Exception as e:
        print("TTS error:", e)
        return None

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("Incoming data:", data)  # <-- debug line

    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0].get("value", {})
        messages = changes.get("messages")

        if not messages:
            print("No message in update.")
            return {"status": "ok"}

        msg = messages[0]
        user_id = msg["from"]

        # Handle text input
        if "text" in msg:
            user_text = msg["text"]["body"]

        # Handle voice input
        elif msg.get("type") == "audio":
            media_id = msg["audio"]["id"]
            media_url = await get_media_url(media_id)
            if not media_url:
                return {"status": "media url failed"}

            user_text = await transcribe_voice(media_url)
            if not user_text:
                return {"status": "transcription failed"}
        else:
            print("Unsupported message type")
            return {"status": "unsupported"}

        # Load chat history
        record = db.get(UserMemory.user_id == user_id)
        chat_history = record["messages"] if record else []
        chat_history.append({"role": "user", "content": user_text})

        # Prune
        if len(chat_history) > 8:
            summary = await summarize_messages(chat_history)
            chat_history = [{"role": "assistant", "content": summary}]

        # Get reply
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[SYSTEM_PROMPT] + chat_history,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()
        chat_history.append({"role": "assistant", "content": reply})

        # Save memory
        if record:
            db.update({"messages": chat_history}, UserMemory.user_id == user_id)
        else:
            db.insert({"user_id": user_id, "messages": chat_history})

        # Generate voice
        voice_bytes = await generate_voice(reply)

        # Send both text and audio
        await send_text_message(user_id, reply)
        if voice_bytes:
            await send_voice_message(user_id, voice_bytes)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

async def get_media_url(media_id: str) -> str:
    try:
        url = f"https://graph.facebook.com/v19.0/{media_id}"
        headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        async with httpx.AsyncClient() as client_http:
            res = await client_http.get(url, headers=headers)
            return res.json().get("url")
    except Exception as e:
        print("Media URL fetch error:", e)
        return None

async def transcribe_voice(media_url: str) -> str:
    try:
        headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        async with httpx.AsyncClient() as client_http:
            audio_res = await client_http.get(media_url, headers=headers)
            audio_bytes = audio_res.content

        with open("input.ogg", "wb") as f:
            f.write(audio_bytes)

        with open("input.ogg", "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f
            )
            return transcript.text
    except Exception as e:
        print("Transcription error:", e)
        return None

async def send_text_message(user_id: str, text: str):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": user_id,
        "type": "text",
        "text": {"body": text}
    }
    async with httpx.AsyncClient() as client_http:
        await client_http.post(url, headers=headers, json=payload)

async def send_voice_message(user_id: str, audio_bytes: bytes):
    try:
        # Step 1: Upload media
        upload_url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        files = {"file": ("voice.ogg", audio_bytes, "audio/ogg")}
        data = {
            "messaging_product": "whatsapp",
            "type": "audio"
        }

        async with httpx.AsyncClient() as client_http:
            res = await client_http.post(upload_url, headers=headers, data=data, files=files)
            media_id = res.json().get("id")

        if not media_id:
            print("Upload failed")
            return

        # Step 2: Send audio message
        msg_url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": user_id,
            "type": "audio",
            "audio": {"id": media_id}
        }
        await client_http.post(msg_url, headers=headers, json=payload)
    except Exception as e:
        print("Voice send error:", e)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
