import os
from openai import OpenAI
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from tinydb import TinyDB, Query
import uvicorn
import httpx

# Load environment variables
load_dotenv()

app = FastAPI()

# OpenAI client
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

# Meta verification
@app.get("/")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"status": "unauthorized"}

# Summary generator
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

# Roman Hindi detection
async def is_roman_hindi(text: str) -> bool:
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": "Is the following sentence written in Roman Hindi (Hindi in Latin script)? Reply only with 'yes' or 'no'."},
                {"role": "user", "content": text}
            ],
            max_tokens=1
        )
        return "yes" in response.choices[0].message.content.strip().lower()
    except:
        return False

# Transliteration
async def transliterate_to_roman(text: str) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": "Transliterate this Hindi message to Roman Hindi (Hindi written in English letters). Do not translate, just convert script."},
                {"role": "user", "content": text}
            ],
            max_tokens=500
        )
        return response.choices[0].message.content.strip()
    except:
        return text

# Webhook handler
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

        # Check if Roman Hindi
        is_roman = await is_roman_hindi(user_text)

        # Load or create memory
        record = db.get(UserMemory.user_id == user_id)
        chat_history = record["messages"] if record else []
        chat_history.append({"role": "user", "content": user_text})

        # Prune if needed
        if len(chat_history) > 8:
            summary = await summarize_messages(chat_history)
            chat_history = [{"role": "assistant", "content": summary}]

        # Call OpenAI API
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[SYSTEM_PROMPT] + chat_history,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()

        # Transliterate if needed
        if is_roman:
            reply = await transliterate_to_roman(reply)

        # Save reply
        chat_history.append({"role": "assistant", "content": reply})
        if record:
            db.update({"messages": chat_history}, UserMemory.user_id == user_id)
        else:
            db.insert({"user_id": user_id, "messages": chat_history})

        # Send reply to WhatsApp
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

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

# Run server
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
