import os
import time
import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
# These will be pulled from GitHub Secrets
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("TELEGRAM_CHAT_ID")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def telegram_listener():
    print("🚀 Telegram Cloud Backend Started...")
    offset = 0
    start_time = time.time()

    # GitHub Actions timeout is 6 hours. 
    # We run for 5 hours and 30 mins (19800 seconds) then exit cleanly.
    while (time.time() - start_time) < 19800:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            resp = requests.get(url, params={"offset": offset, "timeout": 20}, timeout=25).json()

            if not resp.get("ok"): continue

            for update in resp.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg or "text" not in msg: continue

                chat_id = str(msg["chat"]["id"])
                username = msg.get("from", {}).get("username", "Unknown")
                text = msg["text"].strip()

                if text == "/start":
                    supabase.table("subscribers").upsert({"chat_id": chat_id, "username": username}).execute()
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                  json={"chat_id": chat_id, "text": "✅ Subscribed to Railway Monitor via Cloud!"})
                    print(f"New User: {username}")

                elif text == "/stop":
                    supabase.table("subscribers").delete().eq("chat_id", chat_id).execute()
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                  json={"chat_id": chat_id, "text": "❌ Unsubscribed."})

                elif text == "/subscribers" and chat_id == ADMIN_ID:
                    res = supabase.table("subscribers").select("*", count="exact").execute()
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                  json={"chat_id": chat_id, "text": f"👥 Total Subscribers: {res.count}"})
        
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    telegram_listener()