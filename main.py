import os
import openai
import requests
from fastapi import FastAPI, Request
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

openai.api_key = os.getenv("OPENAI_API_KEY")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")

WHATSAPP_API_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == META_VERIFY_TOKEN:
        return int(params["hub.challenge"])
    return "Invalid verification token"

@app.post("/webhook")
async def handle_webhook(request: Request):
    body = await request.json()
    print("Incoming webhook:", body)

    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        messages = value.get("messages")

        if messages:
            message = messages[0]
            user_id = message["from"]
            user_text = message["text"]["body"]

            print("📩 From", user_id + ":", user_text)

            # Generate reply using GPT
            gpt_reply = await ask_openai(user_text)
            print("✅ GPT reply:", gpt_reply)

            # Send reply back to user
            send_whatsapp_message(user_id, gpt_reply)

    except Exception as e:
        print("❌ Error handling message:", e)

    return {"status": "ok"}

async def ask_openai(prompt):
    system_prompt = "You are reysQ, a warm, intelligent AI health companion who guides users through symptoms in a friendly, structured and supportive manner. Keep it human-like."

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    )

    return response['choices'][0]['message']['content']

def send_whatsapp_message(recipient_id, message):
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_id,
        "type": "text",
        "text": {"body": message}
    }

    res = requests.post(WHATSAPP_API_URL, headers=HEADERS, json=payload)
    print("📤 WhatsApp API response:", res.status_code, res.text)
