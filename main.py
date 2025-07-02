from fastapi import FastAPI, Request
import httpx, os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
FROM_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

@app.get("/webhook")
async def verify(req: Request):
    params = dict(req.query_params)
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params["hub.challenge"])
    return "Verification failed"

@app.post("/webhook")
async def webhook(req: Request):
    body = await req.json()
    entry = body.get("entry", [])
    if entry:
        changes = entry[0].get("changes", [])
        if changes:
            value = changes[0].get("value", {})
            messages = value.get("messages", [])
            if messages:
                message = messages[0]
                user_msg = message["text"]["body"]
                user_number = message["from"]
                reply = await generate_reply(user_msg)
                await send_message(user_number, reply)
    return {"status": "ok"}

async def send_message(to, message):
    url = f"https://graph.facebook.com/v18.0/{FROM_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, headers=headers, json=data)

async def generate_reply(user_input):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-4",
                    "messages": [
                        {"role": "system", "content": "You are reysQ, a compassionate AI health assistant. Respond with empathy, helpful suggestions, and ask follow-up questions if needed."},
                        {"role": "user", "content": user_input}
                    ]
                }
            )
            result = response.json()
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        return "Sorry, I'm unable to reply right now. Please try again shortly."