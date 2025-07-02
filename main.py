from fastapi import FastAPI, Request
import openai
import os
from pydantic import BaseModel
from fastapi.responses import JSONResponse
import uvicorn

openai.api_key = os.getenv("OPENAI_API_KEY")

app = FastAPI()


class WhatsAppMessage(BaseModel):
    entry: list


@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
        print("🔔 Incoming WhatsApp message:", body)

        entry = body['entry'][0]
        changes = entry['changes'][0]
        value = changes['value']
        messages = value.get('messages')

        if messages:
            user_message = messages[0]['text']['body']
            sender_id = messages[0]['from']

            print(f"📩 Message from {sender_id}: {user_message}")

            # Construct OpenAI prompt
            system_prompt = "You are reysQ, a compassionate AI health assistant. Respond like a preliminary medical advisor. If symptoms are serious, advise seeing a doctor immediately."

            try:
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message}
                    ]
                )

                reply_text = response['choices'][0]['message']['content']
                print("✅ OpenAI reply:", reply_text)

            except Exception as e:
                print("❌ OpenAI API error:", str(e))
                reply_text = "Sorry, I’m unable to reply right now. Please try again shortly."

            # Send reply back to user using WhatsApp API (not implemented in demo)
            # You can print reply_text to simulate delivery
            print(f"💬 Reply to {sender_id}: {reply_text}")

        return JSONResponse(status_code=200, content={"status": "received"})

    except Exception as e:
        print("❗ Unexpected error:", str(e))
        return JSONResponse(status_code=500, content={"error": "Internal server error"})


@app.get("/")
def home():
    return {"message": "reysQ is live!"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
