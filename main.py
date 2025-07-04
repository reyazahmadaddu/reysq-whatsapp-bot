import os
import httpx
import openai
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
import asyncio

load_dotenv()

app = FastAPI()

# ENV variables
WHATSAPP_TOKEN = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

USE_VOICE = False  # Set to True if you want voice replies
openai.api_key = OPENAI_API_KEY

# Webhook verification for WhatsApp
@app.get("/webhook")
async def verify(request: Request):
    args = dict(request.query_params)
    if args.get("hub.mode") == "subscribe" and args.get("hub.verify_token") == VERIFY_TOKEN:
        return int(args["hub.challenge"])
    return {"error": "Verification failed"}

# Webhook for incoming WhatsApp messages
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("Incoming data:", data)

    try:
        for entry in data["entry"]:
            for change in entry["changes"]:
                value = change.get("value", {})
                messages = value.get("messages", [])
                for message in messages:
                    user_id = message.get("from")
                    message_type = message.get("type")

                    if message_type == "text":
                        text = message.get("text", {}).get("body", "")
                        if text:
                            print("User said:", text)
                            reply = await get_openai_reply(text, user_id)

                            if USE_VOICE:
                                await send_voice_reply(reply, user_id)
                            else:
                                await send_whatsapp_text(reply, user_id)
                        else:
                            print("Text body missing.")
                            await send_whatsapp_text("Mujhe samajh nahi aaya. Dobara kahiyega.", user_id)

                    elif message_type == "audio":
                        await send_whatsapp_text("Audio mila! Par main abhi usse samajhne ki koshish kar raha hoon. 🧐", user_id)

                    else:
                        print("Unsupported message type:", message_type)
                        await send_whatsapp_text("Mujhe yeh samajhne mein dikkat ho rahi hai. Kya aap text bhej sakte hain?", user_id)
    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

# Send text reply to user
async def send_whatsapp_text(message, user_id):
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": user_id,
        "type": "text",
        "text": {"body": message}
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=payload, headers=headers)
        print("WhatsApp reply status:", r.status_code)
        if r.status_code >= 400:
            print("WhatsApp reply error:", r.text)

# Send voice reply using OpenAI TTS
async def send_voice_reply(message, user_id):
    try:
        print("Generating voice reply...")
        audio_response = openai.audio.speech.create(
            model="tts-1",
            voice="nova",  # Female voice
            input=message
        )
        audio_bytes = audio_response.read()

        # Upload to WhatsApp servers
        upload_url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}"
        }
        files = {
            "file": ("speech.ogg", audio_bytes, "audio/ogg")
        }
        data = {
            "messaging_product": "whatsapp",
            "type": "audio/ogg"
        }

        async with httpx.AsyncClient() as client:
            r = await client.post(upload_url, headers=headers, data=data, files=files)
            media_id = r.json().get("id")
            print("Media upload status:", r.status_code, "Media ID:", media_id)

            if not media_id:
                print("Media upload failed:", r.text)
                return

            # Send voice message
            message_payload = {
                "messaging_product": "whatsapp",
                "to": user_id,
                "type": "audio",
                "audio": {
                    "id": media_id
                }
            }
            send_url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
            await client.post(send_url, headers=headers, json=message_payload)

    except Exception as e:
        print("Voice send error:", e)

# Generate AI reply
async def get_openai_reply(prompt, user_id):
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Tum ek pocket doctor ho jiska naam reysQ hai. Emotional bhi ho aur technical bhi."},
                {"role": "user", "content": prompt}
            ]
        )
        reply = response.choices[0].message.content.strip()
        return reply
    except Exception as e:
        print("OpenAI error:", e)
        return "Mujhe thoda waqt dijiye, main wapas aata hoon."

