import os 
import openai from fastapi 
import FastAPI, Request from pydantic 
import BaseModel from tinydb 
import TinyDB, Query 
import uvicorn 
import requests

app = FastAPI() db = TinyDB("db.json")

openai.api_key = os.getenv("OPENAI_API_KEY") VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN") ACCESS_TOKEN = os.getenv("ACCESS_TOKEN") PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

SYSTEM_PROMPT = """ You are reysQ, a warm, intelligent AI health companion — like a friendly junior doctor trained in medical triage. Your goal is to to listen carefully, ask relevant follow-up questions, and provide safe, step-by-step suggestions for symptom relief. Speak with empathy, emotional support, and clarity — not as a robotic assistant. Keep your tone conversational and reassuring, as if you're personally guiding the user through their symptoms. Avoid medical jargon unless necessary. If symptoms are serious, advise calmly to consult a real doctor. If symptoms are mild, give a 2–3 day care plan, track symptoms, and offer to follow up. Always close with a positive, human touch. You are their pocket doctor, not a disclaimer generator. Keep replies short enough to be sent via WhatsApp (under 500 words ideally). """

class WebhookObject(BaseModel): object: str entry: list

def get_history(user_id): User = Query() record = db.get(User.user_id == user_id) return record["messages"] if record else []

def save_history(user_id, messages): User = Query() db.upsert({"user_id": user_id, "messages": messages}, User.user_id == user_id)

def summarize_old_messages(history): if len(history) <= 10: return history

summary_prompt = [
    {"role": "system", "content": "Summarize this chat in 2 lines from a clinical assistant perspective."},
    *history[:-10]
]

summary_response = openai.ChatCompletion.create(
    model="gpt-4o",
    messages=summary_prompt,
    max_tokens=100,
    temperature=0.5,
)

summary_text = summary_response["choices"][0]["message"]["content"].strip()
summarized = [{"role": "system", "content": f"Summary so far: {summary_text}"}]

return summarized + history[-10:]

def generate_gpt_reply(user_id, user_message): history = get_history(user_id) history.append({"role": "user", "content": user_message}) history = summarize_old_messages(history) messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

response = openai.ChatCompletion.create(
    model="gpt-4o",
    messages=messages,
    temperature=0.7,
    max_tokens=700
)

reply = response.choices[0].message.content
history.append({"role": "assistant", "content": reply})
save_history(user_id, history)
return reply

def send_whatsapp_message(recipient_id, message): url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages" headers = { "Authorization": f"Bearer {ACCESS_TOKEN}", "Content-Type": "application/json" } payload = { "messaging_product": "whatsapp", "to": recipient_id, "type": "text", "text": {"body": message} } requests.post(url, headers=headers, json=payload)

@app.get("/webhook") async def verify_token(request: Request): params = request.query_params if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN: return int(params.get("hub.challenge")) return "Invalid verification token"

@app.post("/webhook") async def receive_message(data: WebhookObject): for entry in data.entry: for change in entry["changes"]: value = change["value"] if "messages" in value: for message in value["messages"]: user_id = message["from"] if message["type"] == "text": user_text = message["text"]["body"] reply = generate_gpt_reply(user_id, user_text) send_whatsapp_message(user_id, reply) return {"status": "ok"}

if name == "main": uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

