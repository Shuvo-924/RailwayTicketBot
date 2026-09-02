import os
import time
import requests
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = str(os.getenv("TELEGRAM_CHAT_ID")) # Ensure it's a string for comparison

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

                # --- USER COMMAND: /start ---
                if text == "/start":
                    supabase.table("subscribers").upsert({"chat_id": chat_id, "username": username}).execute()
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                  json={"chat_id": chat_id, "text": "✅ Subscribed to Railway Monitor via Cloud!"})
                    print(f"New User Joined: {username}")

                # --- USER COMMAND: /stop ---
                elif text == "/stop":
                    supabase.table("subscribers").delete().eq("chat_id", chat_id).execute()
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                  json={"chat_id": chat_id, "text": "❌ Unsubscribed. You will no longer receive alerts."})
                    print(f"User Left: {username}")

                # --- USER COMMAND: /status ---
                elif text == "/status":
                    res = supabase.table("subscribers").select("*").eq("chat_id", chat_id).execute()
                    if res.data:
                        status_msg = "🔔 STATUS: Subscribed\nYou are currently on the notification list."
                    else:
                        status_msg = "🔕 STATUS: Not Subscribed\nUse /start to join the list."
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                  json={"chat_id": chat_id, "text": status_msg})

                # --- ADMIN COMMAND: /subscribers (List Users) ---
                elif text == "/subscribers" and chat_id == ADMIN_ID:
                    res = supabase.table("subscribers").select("username, chat_id").execute()
                    if res.data:
                        user_list = "\n".join([f"- @{u['username']} (`{u['chat_id']}`)" for u in res.data])
                        response = f"👥 **Subscribers ({len(res.data)}):**\n\n{user_list}"
                    else:
                        response = "👥 Subscribers: 0"
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                  json={"chat_id": chat_id, "text": response, "parse_mode": "Markdown"})

                # --- ADMIN COMMAND: /add <id> <username> ---
                elif text.startswith("/add ") and chat_id == ADMIN_ID:
                    try:
                        parts = text.split(" ")
                        new_id = parts[1]
                        new_user = parts[2] if len(parts) > 2 else "ManualUser"
                        supabase.table("subscribers").upsert({"chat_id": new_id, "username": new_user}).execute()
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                      json={"chat_id": chat_id, "text": f"✅ Manually added {new_user} ({new_id})"})
                    except Exception as e:
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                      json={"chat_id": chat_id, "text": f"❌ Error: Use `/add ID USERNAME`"})

                # --- ADMIN COMMAND: /remove <id> ---
                elif text.startswith("/remove ") and chat_id == ADMIN_ID:
                    try:
                        rem_id = text.split(" ")[1]
                        supabase.table("subscribers").delete().eq("chat_id", rem_id).execute()
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                      json={"chat_id": chat_id, "text": f"✅ Manually removed ID: {rem_id}"})
                    except Exception as e:
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                                      json={"chat_id": chat_id, "text": "❌ Error: Use `/remove ID`"})

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    telegram_listener()