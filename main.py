import os
import httpx
import uvicorn
import asyncio
import tempfile
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from openai import OpenAI
from tinydb import TinyDB, Query

# Load environment variables
load_dotenv()

app = FastAPI()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

db = TinyDB("memory.json")
UserMemory = Query()

SYSTEM_PROMPT = (
    "You are ReysQ — a warm, emotionally intelligent AI health companion.\n"
    "You assist users in understanding their symptoms and provide support like a pocket doctor.\n"
    "You remember past concerns via a summary and new chat messages.\n"
    "Always be kind, contextual, and helpful. Avoid sounding robotic.\n"
    "Mostly give response in intro-body-conclusion format, often with bullet points. \n"
    "Reply in clear, concise, WhatsApp-suitable language."
)

WELCOME_MESSAGE = (
    "👋 Hi there! I’m *ReysQ*, your AI-enabled Pocket Doctor.\n\n"
    "🧠 I track how you’re feeling and guide you through health concerns.\n"
    "💡 I help with symptoms, medications, test results & more — always with care.\n\n"
    "What’s on your mind today?"
)

# Summarize memory for long chats
async def summarize_conversation(existing_summary: str, messages: List[Dict]) -> str:
    try:
        prompt = [
            {"role": "system", "content": (
                "Summarize this conversation between a health bot and user. "
                "Retain key health concerns, emotional state, past advice, and pending user issues. "
                "Make it useful for ongoing support and follow-up."
            )}
        ]
        if existing_summary:
            prompt.append({"role": "assistant", "content": existing_summary})
        prompt += messages

        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=prompt,
            max_tokens=250
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("Summary error:", e)
        return existing_summary

# Audio transcription
async def transcribe_audio(media_id: str) -> str:
    try:
        headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        url = f"https://graph.facebook.com/v19.0/{media_id}"

        async with httpx.AsyncClient() as client_http:
            media_info = await client_http.get(url, headers=headers)
            media_url = media_info.json().get("url")

            audio_data = await client_http.get(media_url, headers=headers)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
                tmp.write(audio_data.content)
                audio_path = tmp.name

        with open(audio_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        return transcript.text
    except Exception as e:
        print("Transcription error:", e)
        return "Sorry, audio transcription failed."

# Send reply on WhatsApp
async def send_whatsapp(to: str, message: str):
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    try:
        async with httpx.AsyncClient() as client_http:
            await client_http.post(
                f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
                headers=headers,
                json=payload
            )
    except Exception as e:
        print("Send message error:", e)

# Webhook verification
@app.get("/")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"status": "unauthorized"}

# Webhook POST
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0].get("value", {})
        messages = changes.get("messages")
        if not messages:
            return {"status": "no_message"}

        msg = messages[0]
        user_id = msg.get("from")
        msg_type = msg.get("type")

        if not user_id:
            return {"status": "missing_user"}

        # Initialize memory if needed
        record = db.get(UserMemory.user_id == user_id)
        if not record:
            db.insert({"user_id": user_id, "recent": [], "summary": ""})
            await send_whatsapp(user_id, WELCOME_MESSAGE)
            record = db.get(UserMemory.user_id == user_id)

        summary = record.get("summary", "")
        recent = record.get("recent", [])

        if msg_type == "text":
            user_text = msg["text"]["body"]
        elif msg_type == "audio":
            media_id = msg["audio"]["id"]
            user_text = await transcribe_audio(media_id)
        else:
            user_text = "Sorry, unsupported message type."

        # Add user input
        recent.append({"role": "user", "content": user_text})

        # Summarize if message history is long
        if len(recent) > 6:
            summary = await summarize_conversation(summary, recent)
            recent = []

        messages_for_gpt = [{"role": "system", "content": SYSTEM_PROMPT}]
        if summary:
            messages_for_gpt.append({"role": "assistant", "content": summary})
        messages_for_gpt += recent

        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=messages_for_gpt,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()

        recent.append({"role": "assistant", "content": reply})
        db.update({"recent": recent, "summary": summary}, UserMemory.user_id == user_id)

        await send_whatsapp(user_id, reply)

    except Exception as e:
        print("Webhook Error:", e)

    return {"status": "ok"}

# Keep app alive (Render, Fly.io)
async def keep_alive():
    while True:
        try:
            async with httpx.AsyncClient() as client_http:
                await client_http.get("https://reysq.onrender.com/")
        except Exception as e:
            print("Self-ping failed:", e)
        await asyncio.sleep(600)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
