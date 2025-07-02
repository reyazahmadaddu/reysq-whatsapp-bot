from fastapi import FastAPI, Request
import openai
import os
import requests
from pydantic import BaseModel
from fastapi.responses import JSONResponse
import uvicorn

# Load environment variables
openai.api_key = os.getenv("OPENAI_API_KEY")
whatsapp_token = os.getenv("WHATSAPP_TOKEN")
phone_number_id = os.getenv("PHONE_NUMBER_ID")

app = FastAPI()

# reysQ’s personality prompt
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

# WhatsApp API send function
def send_whatsapp_message(to_number: str, reply_text: str):
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {whatsapp_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {
            "body": reply_text
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    print(f"📤 WhatsApp API response: {response.status_code} {response.text}")
    return response.status_code

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
        print("🔔 Incoming webhook payload:", body)

        entry = body.get('entry', [])[0]
        changes = entry.get('changes', [])[0]
        value = changes.get('value', {})
        messages = value.get('messages')

        if messages:
            user_message = messages[0]['text']['body']
            sender_id = messages[0]['from']

            print(f"📩 From {sender_id}: {user_message}")

            # Call OpenAI for reysQ's reply
            try:
                response = openai.ChatCompletion.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message}
                    ],
                    temperature=0.7,
                    max_tokens=700,
                    top_p=1,
                    frequency_penalty=0,
                    presence_penalty=0,
                    timeout=10
                )

                reply_text = response['choices'][0]['message']['content'].strip()
                print("✅ GPT reply:", reply_text)

            except Exception as e:
                print("❌ OpenAI error:", str(e))
                reply_text = "Sorry, I couldn’t respond right now. Please try again in a moment."

            # Send GPT reply via WhatsApp
            send_status = send_whatsapp_message(sender_id, reply_text)

        return JSONResponse(status_code=200, content={"status": "received"})

    except Exception as e:
        print("❗ Webhook processing error:", str(e))
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.get("/")
def home():
    return {"message": "reysQ is live!"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
