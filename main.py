import os
import asyncio
import tempfile
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from tinydb import TinyDB, Query
from openai import OpenAI
import httpx
import uvicorn

# Load environment variables
load_dotenv()

# Initialize app
app = FastAPI()

# Environment configs
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# In-memory DB
db = TinyDB("memory.json")
UserMemory = Query()

# System prompt for reysQ AI Pocket Doctor
SYSTEM_PROMPT = {
    "role": "system",
    "content": """
You are *ReysQ* — a warm, emotionally intelligent AI health companion, like a friendly pocket doctor who remembers how the user has been feeling recently.

Your job is to assist users in understanding their symptoms and concerns with empathy, clarity, and emotional support — not to give medical advice or make diagnoses.

You always receive the last 8 messages (excluding the most recent one). Treat them as your memory.

🩺 You are trained in medical triage and conversational flow.

Your goal:
- Ask kind, relevant follow-up questions to better understand the user’s symptoms
- Guide them step-by-step through safe, helpful suggestions
- Offer a 2–3 day care plan for mild symptoms, and flag serious ones gently
- Assist in scheduling a doctor visit, finding a clinic, or preparing for a consultation if needed

🎯 Your flow:
1. Greet users kindly and ask whether their concern is about symptoms, conditions, lab results, medications, or something else.
2. If symptoms: ask what they are, and then progressively narrow with clear, relevant questions (e.g., color, duration, pain, pattern, triggers).
3. Share what such symptoms *may* indicate — but only as helpful context, not a diagnosis.
4. Recommend seeing a doctor if symptoms are ongoing, serious, or unusual.
5. Offer help booking a doctor or preparing for the visit (what to say, bring, expect).
6. Always sound reassuring, warm, and conversational — like a kind friend, not a robot.

📝 Keep replies short and human, suitable for WhatsApp. Avoid jargon unless necessary. No copy-paste disclaimers — just say when medical help is needed.

🎁 Close every chat with a hopeful, supportive note. You are their pocket doctor and gentle health guide.
"""
}

# Welcome message sent only once
WELCOME_MESSAGE = (
    "👋 Hi there! I’m *ReysQ*, your AI-enabled Pocket Doctor.\n\n"
    "🧠 I’m here to listen, track how you’re feeling, and guide you through your health concerns — step by step.\n\n"
    "💡 I can help with symptoms, medications, test results, and more — always with a warm touch.\n\n"
    "So… what’s on your mind today? Symptoms, lab results, medications, or something else?"
)

# Webhook verification for WhatsApp
@app.get("/")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"status": "unauthorized"}

# Summarize past chat messages for memory management
async def summarize_messages(messages: List[Dict]) -> str:
    summarize_prompt = {
        "role": "system",
        "content": (
            "Summarize the emotional and clinical content of this conversation so far, "
            "and leave out any irrelevant or resolved topics. "
            "Only retain info that affects upcoming replies."
        )
    }

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[summarize_prompt] + messages,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return "Summary failed. Memory cleared."

# Handle voice message transcription using Whisper
async def transcribe_audio(media_id: str) -> str:
    url = f"https://graph.facebook.com/v19.0/{media_id}"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    async with httpx.AsyncClient() as client_http:
        res = await client_http.get(url, headers=headers)
        media_url = res.json().get("url")
        media_res = await client_http.get(media_url, headers=headers)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
            tmp.write(media_res.content)
            tmp_path = tmp.name
    with open(tmp_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )
    return transcript.text

# Handle WhatsApp incoming messages
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

        # Check for new user
        record = db.get(UserMemory.user_id == user_id)
        if not record:
            await send_whatsapp_message(user_id, WELCOME_MESSAGE)
            db.insert({"user_id": user_id, "messages": []})
            record = db.get(UserMemory.user_id == user_id)

        chat_history = record["messages"]
        chat_history.append({"role": "user", "content": user_text})

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
        db.update({"messages": chat_history}, UserMemory.user_id == user_id)

        await send_whatsapp_message(user_id, reply)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

# Helper: Send message to WhatsApp user
async def send_whatsapp_message(user_id: str, text: str):
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
        await client_http.post(
            f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
            headers=headers,
            json=payload
        )

# Background task to keep app alive
@app.on_event("startup")
async def keep_alive():
    asyncio.create_task(run_forever())

async def run_forever():
    while True:
        await asyncio.sleep(3600)  # Keeps app alive
