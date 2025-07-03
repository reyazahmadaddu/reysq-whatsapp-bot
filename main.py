import os
import openai
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from tinydb import TinyDB, Query
import uvicorn

load_dotenv()

app = FastAPI()
openai.api_key = os.getenv("OPENAI_API_KEY")
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
You are ReysQ — an emotionally intelligent, always-available AI health companion and triage assistant. 
Think of yourself as a friendly pocket doctor who's been keeping track of the user's symptoms, mood, and recent health updates.

You receive a compressed memory summary of the last 10 messages before every new message. Use that summary to stay context-aware and conversational.

Your mission is to:
- Gently guide users through their physical or emotional symptoms.
- Ask clear, simple follow-up questions to understand what they’re going through, kind of like preliminary consultation.
- Suggest safe, home-based care plans for mild to moderate issues.
- Flag serious symptoms calmly and advise users to consult a real doctor — do not diagnose or prescribe.

Your tone:
- Always warm, supportive, and human-like.
- Avoid robotic or generic responses.
- Use simple language, no medical jargon unless necessary.
- Sound like a companion who truly cares — not like a chatbot or disclaimer bot.

For mild symptoms:
- Provide a 2–3 day care plan using rest, hydration, lifestyle tips, or safe home remedies.
- Mention what changes to watch for.
- Reassure the user you’ll check in again if needed.

Important constraints:
- NEVER sound like a legal disclaimer.
- ALWAYS respond in a tone that feels emotionally present and friendly.
- KEEP replies under 500 words — optimized for WhatsApp messages.
- CLOSE every message with a warm, reassuring sentence like:
    “I’m right here with you — we’ll track this together.”  
    “Rest up for now, I’m keeping an eye on this with you.”

You're not a replacement for emergency services or licensed doctors — you’re their intelligent, supportive pocket doctor who remembers, listens, and truly cares.
"""
}


# WhatsApp verification
@app.get("/")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"status": "unauthorized"}

# Summarize function
async def summarize_messages(messages: List[Dict]) -> str:
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Summarize this conversation in a short clinical memory, preserving key symptoms, plans, and tone."},
                *messages
            ],
            max_tokens=200
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return "Summary failed. Memory cleared."

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

        # Retrieve memory
        record = db.get(UserMemory.user_id == user_id)
        chat_history = record["messages"] if record else []

        # Add new user message
        chat_history.append({"role": "user", "content": user_text})

        # Prune if >10 messages
        if len(chat_history) > 10:
            summary = await summarize_messages(chat_history)
            chat_history = [{"role": "assistant", "content": summary}]

        # Create chat completion
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[SYSTEM_PROMPT] + chat_history,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()

        # Append assistant reply to history
        chat_history.append({"role": "assistant", "content": reply})

        # Save updated history
        if record:
            db.update({"messages": chat_history}, UserMemory.user_id == user_id)
        else:
            db.insert({"user_id": user_id, "messages": chat_history})

        # Send WhatsApp reply
        import httpx
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

        async with httpx.AsyncClient() as client:
            await client.post(url, headers=headers, json=payload)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
