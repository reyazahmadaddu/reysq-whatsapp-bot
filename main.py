import os
import time
from openai import OpenAI
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from tinydb import TinyDB, Query
import uvicorn
import httpx
import tempfile
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()

app = FastAPI()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# DB
db = TinyDB("memory.json")
UserMemory = Query()

# SYSTEM PROMPT
SYSTEM_PROMPT = {
    "role": "system",
    "content": """
You are *ReysQ* — a warm, emotionally intelligent AI health companion.
Your job is to assist users in understanding their symptoms with clarity and empathy — not to give medical advice.
You always receive the last 8 messages (excluding the most recent one). Treat them as your memory.

🎯 Your flow:
- Greet users kindly and ask whether their concern is about symptoms, conditions, lab results, or meds.
- Ask kind follow-up questions to understand their symptoms better.
- Offer a 2–3 day home care plan if mild.
- Recommend a doctor visit if serious.
- Never repeat empathy too much.
- Close warmly but avoid spamming.
    """
}

WELCOME_MESSAGE = (
    "👋 Hi there! I’m *ReysQ*, your AI-enabled Pocket Doctor.\n\n"
    "🧠 I’m here to listen, track how you’re feeling, and guide you through your health concerns — step by step.\n\n"
    "💡 I can help with symptoms, medications, test results, and more — always with a warm touch.\n\n"
    "So… what’s on your mind today? Symptoms, lab results, medications, or something else?"
)

async def summarize_messages(messages: List[Dict]) -> str:
    summarize_prompt = {
        "role": "system",
        "content": "Summarize the key clinical and emotional details. Leave out anything already resolved or irrelevant."
    }
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[summarize_prompt] + messages,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except:
        return "Summary failed."

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

@app.get("/")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"status": "unauthorized"}

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0].get("value", {})
        messages = changes.get("messages")
        if not messages:
            return {"status": "no message"}

        msg = messages[0]
        user_id = msg.get("from")
        msg_type = msg.get("type")

        if not user_id:
            return {"status": "invalid user"}

        if msg_type == "text":
            user_text = msg.get("text", {}).get("body", "")
        elif msg_type == "audio":
            media_id = msg.get("audio", {}).get("id")
            user_text = await transcribe_audio(media_id)
        else:
            return {"status": "unsupported type"}

        # Ignore meaningless texts
        if user_text.strip().lower() in ["", "hmm", "haan", "?", "kya", "hmmmm", "ok"]:
            return {"status": "ignored"}

        # Fetch or initialize memory
        record = db.get(UserMemory.user_id == user_id)
        if not record:
            headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
            payload = {
                "messaging_product": "whatsapp",
                "to": user_id,
                "type": "text",
                "text": {"body": WELCOME_MESSAGE}
            }
            async with httpx.AsyncClient() as client_http:
                await client_http.post(f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages", headers=headers, json=payload)
            db.insert({"user_id": user_id, "messages": [], "last_reply_time": time.time()})
            record = db.get(UserMemory.user_id == user_id)

        # Cooldown check: skip reply if already responded in last 30s
        if time.time() - record.get("last_reply_time", 0) < 30:
            return {"status": "cooldown"}

        chat_history = record["messages"]
        if len(chat_history) > 8:
            summary = await summarize_messages(chat_history)
            chat_history = [{"role": "assistant", "content": f"Summary so far: {summary}"}]

        chat_history.append({"role": "user", "content": user_text})

        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[SYSTEM_PROMPT] + chat_history,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()

        chat_history.append({"role": "assistant", "content": reply})
        db.update({"messages": chat_history, "last_reply_time": time.time()}, UserMemory.user_id == user_id)

        headers = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json"}
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

# Optional: keep app alive with self-ping (but no reply)
import asyncio

@app.on_event("startup")
async def keep_alive():
    async def ping_loop():
        while True:
            try:
                async with httpx.AsyncClient() as client:
                    await client.get("https://your-fly-app.fly.dev/")  # Replace with your domain
            except:
                pass
            await asyncio.sleep(600)
    asyncio.create_task(ping_loop())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
