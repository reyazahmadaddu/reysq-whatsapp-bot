
import os
from openai import OpenAI
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from tinydb import TinyDB, Query
import uvicorn
import httpx
import re

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

# Language detection
def detect_language(text: str) -> str:
    if re.search(r'[ऀ-ॿ]', text):
        return "hi"  # Hindi (Devanagari)
    elif re.search(r'\b(kya|kaise|nahi|haan|dard|theek|thik|hai|hoon)\b', text.lower()):
        return "roman-hindi"
    else:
        return "en"

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

        detected_lang = detect_language(user_text)

        # Load or create memory
        record = db.get(UserMemory.user_id == user_id)
        chat_history = record["messages"] if record else []
        chat_history.append({"role": "user", "content": user_text})

        # Prune if needed
        if len(chat_history) > 8:
            summary = await summarize_messages(chat_history)
            chat_history = [{"role": "assistant", "content": summary}]

        # Add dynamic language prompt
        language_prompt = None
        if detected_lang == "en":
            language_prompt = {
                "role": "system",
                "content": "The user is speaking in English. Please reply in English using warm and supportive tone like a health companion."
            }
        elif detected_lang == "roman-hindi":
            language_prompt = {
                "role": "system",
                "content": "User is speaking in Roman Hindi. Please reply in Roman Hindi using clear and friendly tone."
            }

        final_messages = [SYSTEM_PROMPT]
        if language_prompt:
            final_messages.append(language_prompt)
        final_messages += chat_history

        # Call OpenAI API
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=final_messages,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()

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
