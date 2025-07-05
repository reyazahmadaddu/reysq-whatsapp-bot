import os
from openai import OpenAI
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from tinydb import TinyDB, Query
import uvicorn
import httpx
import tempfile

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

WELCOME_MESSAGE = (
    "👋 Hey there! I'm *ReysQ*, your personal AI health companion.\n\n"
    "🧠 I'm trained to listen to your health concerns, guide you step-by-step, "
    "and even remember how you've been feeling.\n\n"
    "🚑 I offer safe home remedies, early advice, and emotional support — 24/7.\n\n"
    "I was built with care, to make healthcare accessible and kind.\n"
    "So… how are you feeling today?"
)

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


summarize_prompt = {
    "role": "system",
    "content": (
        "Summarize the emotional and clinical content of this conversation so far, "
        "and leave out any irrelevant or resolved topics. "
        "Only retain info that affects upcoming replies."
    )
}
messages = [summarize_prompt] + messages,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return "Summary failed. Memory cleared."

async def transcribe_audio(media_id: str) -> str:
    # Step 1: Get media URL
    url = f"https://graph.facebook.com/v19.0/{media_id}"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    async with httpx.AsyncClient() as client_http:
        res = await client_http.get(url, headers=headers)
        media_url = res.json().get("url")

        # Step 2: Download the audio file
        media_res = await client_http.get(media_url, headers=headers)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
            tmp.write(media_res.content)
            tmp_path = tmp.name

    # Step 3: Transcribe with Whisper
    with open(tmp_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )
    return transcript.text

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
        user_id = msg["from"]
        msg_type = msg.get("type")

        if msg_type == "text":
            user_text = msg["text"]["body"]

        elif msg_type == "audio":
            media_id = msg["audio"]["id"]
            user_text = await transcribe_audio(media_id)

        else:
            user_text = "Unsupported message type."

        # Check if new user
        record = db.get(UserMemory.user_id == user_id)
        if not record:
            # Send welcome message once
            headers = {
                "Authorization": f"Bearer {ACCESS_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": user_id,
                "type": "text",
                "text": {"body": WELCOME_MESSAGE}
            }
            async with httpx.AsyncClient() as client_http:
                await client_http.post(
                    f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
                    headers=headers,
                    json=payload
                )
            # Create memory for this user
            db.insert({"user_id": user_id, "messages": []})
            record = db.get(UserMemory.user_id == user_id)

        # Retrieve memory
        chat_history = record["messages"]
        chat_history.append({"role": "user", "content": user_text})

        # Summarize if history too long
        if len(chat_history) > 8:
            summary = await summarize_messages(chat_history)
            chat_history = [{"role": "assistant", "content": summary}]

        # Get response from GPT
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[SYSTEM_PROMPT] + chat_history,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()

        # Save memory
        chat_history.append({"role": "assistant", "content": reply})
        db.update({"messages": chat_history}, UserMemory.user_id == user_id)

        # Send reply to WhatsApp
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
            await client_http.post(f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages", headers=headers, json=payload)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
