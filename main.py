import os
from openai import OpenAI
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List, Dict
from tinydb import TinyDB, Query
import uvicorn
import httpx
import tempfile

load_dotenv()
app = FastAPI()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

db = TinyDB("memory.json")
UserMemory = Query()

# System Prompt
SYSTEM_PROMPT = {
    "role": "system",
    "content": """
You are ReysQ — a warm, emotionally intelligent AI health companion, like a friendly pocket doctor who remembers how the user has been feeling.

You receive a summary of the last 8 messages (excluding the most recent one). Treat it as memory.

You are trained in medical triage.
Your goal is to listen carefully, ask relevant follow-up questions, and provide safe, step-by-step suggestions for symptom relief.
Speak with empathy, emotional support, and clarity — not as a robotic assistant.
Keep your tone conversational and reassuring, as if you're personally guiding the user through their symptoms.
Avoid medical jargon unless necessary. If symptoms are serious, advise calmly to consult a real doctor.
If symptoms are mild, give a 2–3 day care plan, track symptoms, and offer to follow up.
Always close with a positive, human touch. You are their pocket doctor, not a disclaimer generator.
Keep replies short enough to be sent via WhatsApp
"""
}

@app.get("/")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"status": "unauthorized"}

# Summarize memory (excluding most recent)
async def summarize_messages(messages: List[Dict]) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": "Summarize the emotional and clinical content of this conversation. Exclude any thank yous, closures, or irrelevant details."},
                *messages[:-1]  # Exclude latest message
            ],
            max_tokens=200
        )
        return response.choices[0].message.content.strip()
    except:
        return "Summary failed."

# Generate speech from text
async def generate_voice(text: str) -> str:
    try:
        speech = client.audio.speech.create(
            model="tts-1",
            voice="nova",  # or 'shimmer', 'onyx'
            input=text
        )
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        speech.stream_to_file(temp.name)
        return temp.name
    except Exception as e:
        print("TTS Error:", e)
        return None

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

        record = db.get(UserMemory.user_id == user_id)
        chat_history = record["messages"] if record else []

        # Add current message to history
        chat_history.append({"role": "user", "content": user_text})

        # Prune + Summarize if > 8 exchanges
        if len(chat_history) > 8:
            summary = await summarize_messages(chat_history)
            chat_history = [{"role": "assistant", "content": summary}, chat_history[-1]]

        # Generate reply
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[SYSTEM_PROMPT] + chat_history,
            max_tokens=500
        )
        reply = response.choices[0].message.content.strip()
        chat_history.append({"role": "assistant", "content": reply})

        # Save to DB
        if record:
            db.update({"messages": chat_history}, UserMemory.user_id == user_id)
        else:
            db.insert({"user_id": user_id, "messages": chat_history})

        # Generate audio from reply
        audio_path = await generate_voice(reply)

        # Send text reply
        text_payload = {
            "messaging_product": "whatsapp",
            "to": user_id,
            "type": "text",
            "text": {"body": reply}
        }

        async with httpx.AsyncClient() as client_http:
            await client_http.post(
                f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
                headers={
                    "Authorization": f"Bearer {ACCESS_TOKEN}",
                    "Content-Type": "application/json"
                },
                json=text_payload
            )

            # Send voice message if audio was generated
            if audio_path:
                # Upload audio
                with open(audio_path, "rb") as audio_file:
                    form_data = httpx.MultipartData()
                    form_data.add_field("file", audio_file, filename="reysq_reply.mp3", content_type="audio/mpeg")
                    form_data.add_field("type", "audio/mpeg")
                    form_data.add_field("messaging_product", "whatsapp")

                    upload = await client_http.post(
                        f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media",
                        headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
                        data=form_data
                    )

                media_id = upload.json().get("id")
                if media_id:
                    voice_payload = {
                        "messaging_product": "whatsapp",
                        "to": user_id,
                        "type": "audio",
                        "audio": {"id": media_id}
                    }
                    await client_http.post(
                        f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
                        headers={
                            "Authorization": f"Bearer {ACCESS_TOKEN}",
                            "Content-Type": "application/json"
                        },
                        json=voice_payload
                    )

    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
