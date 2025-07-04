import os
import httpx
import tempfile
from fastapi import FastAPI, Request
from dotenv import load_dotenv
from tinydb import TinyDB, Query
from openai import OpenAI
from typing import List, Dict
from pydantic import BaseModel
from unidecode import unidecode

# Load env vars
load_dotenv()
app = FastAPI()

# Init OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Meta details
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# DB setup
db = TinyDB("memory.json")
UserMemory = Query()

# System prompt
SYSTEM_PROMPT = {
    "role": "system",
    "content": """
You are ReysQ — a warm, emotionally intelligent AI health companion who remembers how the user has been feeling.

Before every reply, you receive a summary of the last 8 messages. Treat it as your memory and context.

Your role:
- Gently guide users through symptoms with empathy.
- Ask caring follow-up questions.
- Suggest safe, home-based care for mild/moderate issues.
- Flag serious symptoms calmly. Never diagnose/prescribe.

Tone:
- Always supportive and clear.
- Use Hindi, Bhojpuri or Hinglish based on user tone.
- If user uses Roman Hindi, reply in Roman Hindi too.

Examples:
- “Kya haal hai?” → “Aap kaise ho? Aaram mila thoda?”
- “Sir dard ho raha hai” → “Kya aur koi symptoms bhi hain?”

End replies with reassurance.
"""
}

@app.get("/")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge"))
    return {"status": "unauthorized"}

async def summarize_messages(messages: List[Dict]) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[
                {"role": "system", "content": "Summarize this conversation into a short, clinical memory keeping key feelings, symptoms, and tone."},
                *messages
            ],
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("Summary error:", e)
        return "Summary failed. Memory cleared."

async def send_voice_reply(audio_path: str, user_id: str):
    try:
        # Upload voice media
        with open(audio_path, "rb") as f:
            files = {'file': ("voice.ogg", f, "audio/ogg")}
            params = {"messaging_product": "whatsapp", "type": "audio/ogg"}
            headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
            upload_resp = httpx.post(
                f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media",
                params=params, headers=headers, files=files
            )
        media_id = upload_resp.json().get("id")

        # Send audio message
        payload = {
            "messaging_product": "whatsapp",
            "to": user_id,
            "type": "audio",
            "audio": {"id": media_id}
        }
        async with httpx.AsyncClient() as client:
            await client.post(f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages", headers=headers, json=payload)
    except Exception as e:
        print("Voice send error:", e)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    print("Incoming data:", data)

    try:
        entry = data.get("entry", [])[0]
        changes = entry["changes"][0]["value"]
        messages = changes.get("messages")
        if not messages:
            print("No message in update.")
            return {"status": "ok"}

        msg = messages[0]
        user_id = msg["from"]
        msg_type = msg.get("type")

        if msg_type == "audio":
            return {"status": "audio_received_but_not_handled_yet"}  # Optional: implement STT

        user_text = msg.get("text", {}).get("body")
        if not user_text:
            print("No text in message")
            return {"status": "ok"}

        # Detect Roman Hindi
        is_roman = user_text.isascii() and any(char.isalpha() for char in user_text)

        # Retrieve chat history
        record = db.get(UserMemory.user_id == user_id)
        chat_history = record["messages"] if record else []
        chat_history.append({"role": "user", "content": user_text})

        # Prune history if needed
        if len(chat_history) > 8:
            summary = await summarize_messages(chat_history)
            chat_history = [{"role": "assistant", "content": summary}]

        # Get reply
        completion = client.chat.completions.create(
            model="gpt-3.5-turbo-1106",
            messages=[SYSTEM_PROMPT] + chat_history,
            max_tokens=500
        )
        reply = completion.choices[0].message.content.strip()

        # Save to memory
        chat_history.append({"role": "assistant", "content": reply})
        if record:
            db.update({"messages": chat_history}, UserMemory.user_id == user_id)
        else:
            db.insert({"user_id": user_id, "messages": chat_history})

        # Create TTS voice (female) if Roman detected
        if is_roman:
            speech = client.audio.speech.create(
                model="tts-1",
                voice="nova",  # Female voice
                input=reply
            )
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_audio:
                temp_audio.write(speech.content)
                audio_path = temp_audio.name
            await send_voice_reply(audio_path, user_id)
        else:
            # Send text reply
            async with httpx.AsyncClient() as client_http:
                await client_http.post(
                    f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
                    headers={
                        "Authorization": f"Bearer {ACCESS_TOKEN}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "messaging_product": "whatsapp",
                        "to": user_id,
                        "type": "text",
                        "text": {"body": reply}
                    }
                )
    except Exception as e:
        print("Error:", e)

    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000)
