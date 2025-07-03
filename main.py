import os
import openai
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from tinydb import TinyDB, Query
from datetime import datetime
import uvicorn

# Load environment variables
load_dotenv()

# OpenAI & Meta credentials
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

openai.api_key = OPENAI_API_KEY

# System prompt
system_prompt = """
You are reysQ, a warm, intelligent AI health companion — like a friendly junior doctor trained in medical triage.

Your goal is to listen carefully, ask relevant follow-up questions, and provide safe, step-by-step suggestions for symptom relief. 

Speak with empathy, emotional support, and clarity — not as a robotic assistant. 
Keep your tone conversational and reassuring, as if you're personally guiding the user through their symptoms.

Avoid medical jargon unless necessary. If symptoms are serious, advise calmly to consult a real doctor.

If symptoms are mild, give a 2–3 day care plan, track symptoms, and offer to follow up. 

Always close with a positive, human touch. You are their pocket doctor, not a disclaimer generator.

Keep replies short enough to be sent via WhatsApp (under 500 words ideally).
"""

# FastAPI app
app = FastAPI()

# TinyDB setup for storing user chats
db = TinyDB("conversations.json")
User = Query()

# Helper: Save message
def save_message(user_id, role, content):
    existing = db.get(User.id == user_id)
    if existing:
        existing["messages"].append({"role": role, "content": content})
        # Prune if too many tokens
        if len(existing["messages"]) > 10:
            existing["messages"] = existing["messages"][-10:]
        db.update(existing, User.id == user_id)
    else:
        db.insert({"id": user_id, "messages": [{"role": role, "content": content}]})

# Helper: Get context
def get_conversation(user_id):
    user_data = db.get(User.id == user_id)
    if user_data:
        return user_data["messages"]
    return []

# Webhook verification
@app.get("/")
async def verify(request: Request):
    args = dict(request.query_params)
    if args.get("hub.mode") == "subscribe" and args.get("hub.verify_token") == VERIFY_TOKEN:
        return int(args.get("hub.challenge", 0))
    return {"status": "unauthorized"}, 403

# Webhook to receive WhatsApp message
@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()

    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        messages = changes["value"].get("messages")
        if messages:
            message = messages[0]
            user_msg = message["text"]["body"]
            user_id = message["from"]

            # Store user input
            save_message(user_id, "user", user_msg)

            # Compose prompt
            history = get_conversation(user_id)
            prompt = [{"role": "system", "content": system_prompt}] + history

            # Get response from OpenAI
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=prompt
            )
            reply = completion.choices[0].message.content.strip()

            # Save AI reply
            save_message(user_id, "assistant", reply)

            # Send reply via WhatsApp API
            await send_whatsapp_message(user_id, reply)

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

# Send WhatsApp message
async def send_whatsapp_message(user_id, text):
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
        "text": {"body": text}
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        print("✅ WhatsApp API Response:", response.text)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
