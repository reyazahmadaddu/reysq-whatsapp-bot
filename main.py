import os
import time
import httpx
import tempfile
from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import List, Dict
from dotenv import load_dotenv
from openai import OpenAI
from tinydb import TinyDB, Query
import uvicorn

# Load env vars
load_dotenv()

app = FastAPI()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
PING_URL = os.getenv("PING_URL")  # Add your Render URL here

# DB Setup
db = TinyDB("memory.json")
UserMemory = Query()

# ReysQ System Prompt
SYSTEM_PROMPT = {
    "role": "system",
    "content": """
You are *ReysQ* — a warm, emotionally intelligent AI health companion, like a friendly pocket doctor who remembers how the user has been feeling recently.

Your job is to assist users in understanding their symptoms and concerns with empathy, clarity, and emotional support — not to give medical advice or make diagnoses.

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

WELCOME_MESSAGE = (
    "👋 Hi there! I’m *ReysQ*, your AI-enabled Pocket Doctor.\n\n"
    "🧠 I’m here to listen, track how you’re feeling, and guide you through your health concerns — step by step.\n\n"
    "💡 I can help with symptoms, medications, test results, and more — always with a warm touch.\n\n"
    "So… what’s on your mind today? Symptoms, lab results, medications, or something else?"
)

JUNK_INPUTS = {"hmm", "kya hua", "?", "."}

async def summarize_conversation(summary: str, recent_msgs: List[Dict]) -> str:
    messages = [
        {"role": "system", "content": "Update the summary with recent messages. Keep it under 100 words."},
        {"role": "assistant", "content": f"Previous summary: {summary}"},
        *recent_msgs
    ]
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=messages,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except:
        return summary  # fallback to old summary

async def transcribe_audio(media_id: str) -> str:
    url = f"https://graph.facebook.com/v19.0/{media_id}"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    async with httpx.AsyncClient() as http:
        res = await http.get(url, headers=headers)
        media_url = res.json().get("url")
        media = await http.get(media_url, headers=headers)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
            tmp.write(media.content)
            path = tmp.name
    with open(path, "rb") as audio:
        transcript = client.audio.transcriptions.create(model="whisper-1", file=audio)
    return transcript.text

@app.get("/")
async def verify(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"status": "unauthorized"}

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    try:
        msg = data["entry"][0]["changes"][0]["value"].get("messages", [])[0]
        user_id = msg["from"]
        msg_type = msg.get("type")
        text = msg["text"]["body"] if msg_type == "text" else await transcribe_audio(msg["audio"]["id"])
        if text.lower().strip() in JUNK_INPUTS:
            return {"status": "ignored"}

        record = db.get(UserMemory.user_id == user_id)
        now = time.time()

        if record and now - record.get("last_reply_time", 0) < 30:
            return {"status": "cooldown"}

        if not record:
            record = {
                "user_id": user_id,
                "summary": "",
                "recent_messages": [],
                "last_reply_time": now
            }
            db.insert(record)
            await send_whatsapp(user_id, WELCOME_MESSAGE)

        # Build memory
        summary = record.get("summary", "")
        recent = record.get("recent_messages", [])
        recent.append({"role": "user", "content": text})

        context = [
            {"role": "assistant", "content": f"Summary so far: {summary}"},
            *recent
        ]

        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[SYSTEM_PROMPT] + context,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()

        recent.append({"role": "assistant", "content": reply})

        if len(recent) > 10:
            summary = await summarize_conversation(summary, recent)
            recent = []

        db.update({
            "summary": summary,
            "recent_messages": recent,
            "last_reply_time": now
        }, UserMemory.user_id == user_id)

        await send_whatsapp(user_id, reply)

    except Exception as e:
        print("Error:", e)
    return {"status": "ok"}

async def send_whatsapp(user_id: str, msg: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": user_id,
        "type": "text",
        "text": {"body": msg}
    }
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as http:
        await http.post(f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages", headers=headers, json=payload)

# Self-ping every 14 minutes
import asyncio
@app.on_event("startup")
async def keep_alive():
    async def ping():
        while True:
            try:
                if PING_URL:
                    async with httpx.AsyncClient() as http:
                        await http.get(PING_URL)
                        print("Pinged self")
            except Exception as e:
                print("Ping failed:", e)
            await asyncio.sleep(780)  # 13 mins
    asyncio.create_task(ping())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
