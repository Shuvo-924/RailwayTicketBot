import os
import re
import time
import uuid
import secrets
import smtplib
import threading
import requests

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer

from supabase import create_client
from dotenv import load_dotenv


load_dotenv()


# ============================================================
# CONFIG
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = str(os.getenv("TELEGRAM_CHAT_ID", ""))

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "Shuvo-924")
GITHUB_REPO = os.getenv("GITHUB_REPO", "RailwayTicketBot")
GITHUB_WORKFLOW = os.getenv("GITHUB_WORKFLOW", "search.yml")
GITHUB_REF = os.getenv("GITHUB_REF", "main")


# Email configuration
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD")


supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ============================================================
# CONSTANTS
# ============================================================

# SUST student email:
# Example:
# 2023331XXX@student.sust.edu
#
# Currently requiring exactly 10 digits before @.
SUST_EMAIL_REGEX = re.compile(r"^[0-9]{10}@student\.sust\.edu$", re.IGNORECASE)


VERIFICATION_EXPIRY_MINUTES = 10
MAX_VERIFICATION_ATTEMPTS = 5


# Temporary conversation state.
#
# Example:
#
# USER_STATES[chat_id] = {
#     "step": "email",
#     "email": "...",
#     "code": "123456",
#     ...
# }
#
# This is only temporary state.
# Actual verified users/jobs are stored in Supabase.
USER_STATES = {}


# ============================================================
# HEALTH CHECK
# ============================================================


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Railway Monitor Bot is alive")

    def log_message(self, format, *args):
        # Don't spam logs with health checks
        return


def run_health_server():

    port = int(os.environ.get("PORT", 8080))

    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)

    print(f"Health check server running on port {port}")

    server.serve_forever()


# ============================================================
# TELEGRAM HELPERS
# ============================================================


def telegram_request(method, payload=None):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"

    try:
        response = requests.post(url, json=payload or {}, timeout=20)

        return response.json()

    except Exception as e:
        print(f"Telegram error: {e}")

        return {"ok": False}


def send_message(chat_id, text, reply_markup=None):

    payload = {"chat_id": chat_id, "text": text}

    if reply_markup:
        payload["reply_markup"] = reply_markup

    return telegram_request("sendMessage", payload)


# ============================================================
# MAIN MENU
# ============================================================


def main_menu():

    return {
        "keyboard": [
            [{"text": "🚆 New Search"}, {"text": "📋 My Searches"}],
            [{"text": "❌ Cancel Search"}, {"text": "ℹ️ Help"}],
        ],
        "resize_keyboard": True,
    }


# ============================================================
# EMAIL
# ============================================================


BREVO_API_KEY = os.getenv("BREVO_API_KEY")

def send_verification_email(email, code):
    if not BREVO_API_KEY:
        print("❌ ERROR: BREVO_API_KEY not found.")
        return False

    url = "https://api.brevo.com/v3/smtp/email"
    
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": BREVO_API_KEY
    }
    
    payload = {
        "sender": {
            "name": "Railway Monitor",
            "email": SMTP_EMAIL # This must be the email you signed up with on Brevo
        },
        "to": [{"email": email}],
        "subject": "Railway Monitor - Verification Code",
        "htmlContent": f"""
        <html>
            <body>
                <h1>Verification Code</h1>
                <p>Your Railway Ticket Monitor code is: <strong>{code}</strong></p>
                <p>This code expires in 10 minutes.</p>
            </body>
        </html>
        """
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code in [200, 201, 202]:
            print(f"✅ Success: Email sent to {email}")
            return True
        else:
            print(f"❌ Brevo Error: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Request Error: {e}")
        return False


# ============================================================
# VERIFICATION CODE
# ============================================================


def generate_verification_code():

    return f"{secrets.randbelow(1000000):06d}"


def create_verification(chat_id, email):

    code = generate_verification_code()

    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_EXPIRY_MINUTES)
    ).isoformat()

    # Remove previous codes for this chat/email
    try:
        supabase.table("verification_codes").delete().eq("chat_id", chat_id).execute()

    except Exception as e:
        print(f"Could not remove old verification codes: {e}")

    # Store new code
    try:
        supabase.table("verification_codes").insert(
            {
                "chat_id": chat_id,
                "email": email,
                "code": code,
                "expires_at": expires_at,
                "attempts": 0,
            }
        ).execute()

    except Exception as e:
        print(f"Could not store verification code: {e}")

        return False

    return send_verification_email(email, code)


def verify_code(chat_id, code, username):

    try:
        result = (
            supabase.table("verification_codes")
            .select("*")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return False, "No verification code found."

        verification = result.data[0]

        # ----------------------------------------------------
        # Check attempts
        # ----------------------------------------------------

        attempts = verification.get("attempts", 0)

        if attempts >= MAX_VERIFICATION_ATTEMPTS:
            return False, ("Too many incorrect attempts.\n\nPlease request a new code.")

        # ----------------------------------------------------
        # Check expiry
        # ----------------------------------------------------

        expires_at = datetime.fromisoformat(verification["expires_at"])

        # Handle timestamps ending with Z
        if expires_at.tzinfo:
            now = datetime.now(expires_at.tzinfo)
        else:
            now = datetime.utcnow()

        if now > expires_at:
            return False, (
                "⏰ This verification code has expired.\n\nPlease request a new one."
            )

        # ----------------------------------------------------
        # Check code
        # ----------------------------------------------------

        if code.strip() != verification["code"]:
            supabase.table("verification_codes").update({"attempts": attempts + 1}).eq(
                "chat_id", chat_id
            ).execute()

            remaining = MAX_VERIFICATION_ATTEMPTS - attempts - 1

            return False, (
                f"❌ Incorrect verification code.\n\nAttempts remaining: {remaining}"
            )

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        email = verification["email"]

        # Mark subscriber as verified
        supabase.table("subscribers").upsert(
            {
                "chat_id": chat_id,
                "username": username,  # Capture the actual username
                "email": email,
                "verified": True,
            }
        ).execute()

        # Delete used code
        supabase.table("verification_codes").delete().eq("chat_id", chat_id).execute()

        return True, email

    except Exception as e:
        print(f"Verification error: {e}")

        return False, "Verification failed."


# ============================================================
# CHECK USER VERIFICATION
# ============================================================


def get_verified_user(chat_id):

    try:
        result = (
            supabase.table("subscribers")
            .select("*")
            .eq("chat_id", chat_id)
            .eq("verified", True)
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception as e:
        print(f"Could not check user verification: {e}")

    return None


# ============================================================
# CLASS PARSER
# ============================================================

CLASS_ALIASES = {
    "SNIGDHA": "SNIGDHA",
    "SNIG": "SNIGDHA",
    "S_CHAIR": "S_CHAIR",
    "S CHAIR": "S_CHAIR",
    "SCHAIR": "S_CHAIR",
    "AC_B": "AC_B",
    "AC B": "AC_B",
    "AC_S": "AC_S",
    "AC S": "AC_S",
    "F_BERTH": "F_BERTH",
    "F BERTH": "F_BERTH",
    "F_SEAT": "F_SEAT",
    "F SEAT": "F_SEAT",
    "F_CHAIR": "F_CHAIR",
    "F CHAIR": "F_CHAIR",
}


def parse_classes(text):
    """
    Examples:

        Snigdha
        S_Chair
        Snigdha + S_Chair
        Snigdha, S_Chair
        Snigdha + AC_B + S_Chair

    Returns:

        [
            "SNIGDHA",
            "S_CHAIR"
        ]

    and:

        "SNIGDHA|S_CHAIR"
    """

    # Allow +, comma, or semicolon
    parts = re.split(r"\s*(?:\+|,|;)\s*", text.strip())

    selected = []

    for part in parts:
        normalized = part.strip().upper()

        if not normalized:
            continue

        if normalized not in CLASS_ALIASES:
            return None

        canonical = CLASS_ALIASES[normalized]

        if canonical not in selected:
            selected.append(canonical)

    if not selected:
        return None

    # This becomes the regex used by the monitor.
    regex = "|".join(re.escape(x) for x in selected)

    return selected, regex


def get_queue_position():
    try:
        res = (
            supabase.table("monitoring_jobs")
            .select("id")
            .eq("status", "running")
            .eq("is_private", False)
            .execute()
        )
        return len(res.data)
    except:
        return 0


# ============================================================
# DATE VALIDATION
# ============================================================


def validate_date(date_text):

    try:
        date = datetime.strptime(date_text, "%Y-%m-%d")

        if date.date() < datetime.now().date():
            return False

        return True

    except ValueError:
        return False


# ============================================================
# GITHUB ACTIONS
# ============================================================


def dispatch_github_workflow(
    job_id, chat_id, username, from_s, to_s, date, s_class, phone, pwd, trains
):

    url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}/actions/"
        f"workflows/{GITHUB_WORKFLOW}/dispatches"
    )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    payload = {
        "ref": GITHUB_REF,
        "inputs": {
            "job_id": job_id,
            "chat_id": chat_id,
            "username": username,
            "from_station": from_s,
            "to_station": to_s,
            "journey_date": date,
            "seat_class": s_class,
            "user_phone": phone,
            "user_pass": pwd,
            "desired_trains": trains,
        },
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)

        if response.status_code == 204:
            print(f"GitHub workflow dispatched for job {job_id}")

            return True

        print("GitHub dispatch failed:", response.status_code, response.text)

        return False

    except Exception as e:
        print(f"GitHub dispatch exception: {e}")

        return False


# ============================================================
# CREATE MONITORING JOB
# ============================================================


def create_job(chat_id, username, from_station, to_station, journey_date, seat_class):

    job_id = str(uuid.uuid4())

    try:
        result = (
            supabase.table("monitoring_jobs")
            .insert(
                {
                    "id": job_id,
                    "chat_id": chat_id,
                    "username": username,
                    "from_station": from_station,
                    "to_station": to_station,
                    "journey_date": journey_date,
                    "seat_class": seat_class,
                    "status": "starting",
                }
            )
            .execute()
        )

        if not result.data:
            return None

        return job_id

    except Exception as e:
        print(f"Could not create job: {e}")

        return None


# ============================================================
# CANCEL JOB
# ============================================================


def cancel_github_run(run_id):

    if not run_id:
        return False

    url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}/actions/"
        f"runs/{run_id}/cancel"
    )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        response = requests.post(url, headers=headers, timeout=20)

        return response.status_code in (202, 204)

    except Exception as e:
        print(f"Could not cancel GitHub run: {e}")

        return False


# ============================================================
# USER STATE HELPERS
# ============================================================


def set_state(chat_id, step, **data):

    USER_STATES[chat_id] = {"step": step, **data}


def clear_state(chat_id):

    USER_STATES.pop(chat_id, None)


# ============================================================
# START NEW SEARCH
# ============================================================


def start_new_search(chat_id):
    user = get_verified_user(chat_id)
    if not user:
        send_message(chat_id, "🔒 You need to verify your SUST student email first.\n\nUse /start to verify.")
        return

    # Set state to search_mode first
    set_state(chat_id, "search_mode")

    # Create a keyboard for the mode selection
    markup = {
        "keyboard": [
            [{"text": "🤝 Shared Session"}, {"text": "🔐 Private Session"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

    send_message(
        chat_id, 
        "🛡️ Choose Search Mode:\n\n"
        "🤝 Shared: Uses server account. (Subject to queue)\n"
        "🔐 Private: Uses your own Railway account. (Instant start)",
        reply_markup=markup
    )


# ============================================================
# PROCESS SEARCH
# ============================================================


def process_search_message(chat_id, username, text):

    state = USER_STATES.get(chat_id)

    if not state:
        return False

    step = state["step"]

    state = USER_STATES.get(chat_id)
    if not state:
        return False
    step = state["step"]

    # 1. Choose Mode
    if step == "search_mode":
        if "Private" in text:
            set_state(chat_id, "railway_phone", is_private=True)
            send_message(
                chat_id,
                "🔐 Private Session Selected.\n\nPlease enter your Railway Mobile Number:",
                reply_markup={"remove_keyboard": True} # Clear the mode buttons
            )
        else:
            queue_pos = get_queue_position()
            set_state(chat_id, "from_station", is_private=False)
            send_message(
                chat_id,
                f"🤝 Shared Session Selected.\nThere are currently {queue_pos} searches in queue.\n\nEnter FROM station:",
                reply_markup={"remove_keyboard": True} # Clear the mode buttons
            )
        return True

    # 2. Collect Private Credentials
    if step == "railway_phone":
        set_state(chat_id, "railway_password", is_private=True, phone=text)
        send_message(
            chat_id,
            "Enter your Railway Password (this will be deleted immediately after starting):",
        )
        return True

    if step == "railway_password":
        set_state(
            chat_id,
            "from_station",
            is_private=True,
            phone=state["phone"],
            password=text,
        )
        send_message(chat_id, "Station details time!\n\nEnter FROM station:")
        return True

    # --------------------------------------------------------
    # FROM
    # --------------------------------------------------------

    if step == "from_station":
        set_state(chat_id, "to_station", from_station=text)

        send_message(chat_id, "📍 Enter your TO station.\n\nExample:\nChattogram")

        return True

    # --------------------------------------------------------
    # TO
    # --------------------------------------------------------

    if step == "to_station":
        set_state(
            chat_id, "journey_date", from_station=state["from_station"], to_station=text
        )

        send_message(
            chat_id,
            "📅 Enter journey date.\n\nFormat:\nYYYY-MM-DD\n\nExample:\n2026-09-20",
        )

        return True

    # --------------------------------------------------------
    # DATE
    # --------------------------------------------------------

    if step == "journey_date":
        if not validate_date(text):
            send_message(
                chat_id,
                "❌ Invalid date.\n\n"
                "Please use YYYY-MM-DD and make sure "
                "the date isn't in the past.",
            )

            return True

        set_state(
            chat_id,
            "seat_class",
            from_station=state["from_station"],
            to_station=state["to_station"],
            journey_date=text,
        )

        send_message(
            chat_id,
            "💺 Enter class(es).\n\n"
            "You can select multiple classes using +.\n\n"
            "Examples:\n"
            "• Snigdha\n"
            "• S_Chair\n"
            "• Snigdha + S_Chair\n"
            "• Snigdha + AC_B + S_Chair\n\n"
            "Available:\n"
            "SNIGDHA\n"
            "S_CHAIR\n"
            "AC_B\n"
            "AC_S\n"
            "F_BERTH\n"
            "F_SEAT\n"
            "F_CHAIR",
        )

        return True

    # --------------------------------------------------------
    # CLASS
    # --------------------------------------------------------

    if step == "seat_class":
        parsed = parse_classes(text)

        if not parsed:
            send_message(
                chat_id,
                "❌ I couldn't understand the class.\n\n"
                "Examples:\n"
                "Snigdha\n"
                "S_Chair\n"
                "Snigdha + S_Chair",
            )

            return True

        selected_classes, class_regex = parsed

        set_state(
            chat_id,
            "desired_trains",
            from_station=state["from_station"],
            to_station=state["to_station"],
            journey_date=state["journey_date"],
            seat_class=class_regex,
            class_display=" + ".join(selected_classes)
        )

        markup = {
            "keyboard": [[{"text": "All Trains"}]],
            "resize_keyboard": True,
            "one_time_keyboard": True
        }

        send_message(
            chat_id,
            "🚆 **Which trains do you want to monitor?**\n\n"
            "Enter train names separated by + (e.g., `Parabat + Upavan`)\n"
            "Or click the button below to monitor all trains.",
            reply_markup=markup
        )
        return True

    if step == "desired_trains":
        trains_text = text.strip()
        display_trains = "All Trains" if trains_text.upper() == "ALL TRAINS" else trains_text

        from_station = state["from_station"]
        to_station = state["to_station"]
        journey_date = state["journey_date"]
        class_display = state["class_display"]

        send_message(
            chat_id,
            "🔎 **Confirm your search:**\n\n"
            f"From: {from_station}\n"
            f"To: {to_station}\n"
            f"Date: {journey_date}\n"
            f"Class: {class_display}\n"
            f"Trains: {display_trains}\n\n"
            "Type YES to start the monitor\n"
            "or NO to cancel.",
            reply_markup={"remove_keyboard": True},
        )

        set_state(
            chat_id,
            "confirmation",
            from_station=from_station,
            to_station=to_station,
            journey_date=journey_date,
            seat_class=state["seat_class"],
            class_display=class_display,
            desired_trains=trains_text if trains_text.upper() != "ALL TRAINS" else "ALL"
        )
        return True

        from_station = state["from_station"]
        to_station = state["to_station"]
        journey_date = state["journey_date"]

        class_display = " + ".join(selected_classes)

        send_message(
            chat_id,
            "🔎 Please confirm your search:\n\n"
            f"From: {from_station}\n"
            f"To: {to_station}\n"
            f"Date: {journey_date}\n"
            f"Class: {class_display}\n\n"
            f"Regex: `{class_regex}`\n\n"
            "Type YES to start the monitor\n"
            "or NO to cancel.",
            reply_markup={"remove_keyboard": True},
        )

        set_state(
            chat_id,
            "confirmation",
            from_station=from_station,
            to_station=to_station,
            journey_date=journey_date,
            seat_class=class_regex,
            class_display=class_display,
        )

        return True

    # --------------------------------------------------------
    # CONFIRMATION
    # --------------------------------------------------------

    if step == "confirmation":
        if text.upper() in ("YES", "Y"):
            job_id = str(uuid.uuid4())
            is_priv = state.get("is_private", False)

            # 1. Create Job in Supabase
            supabase.table("monitoring_jobs").insert({
                "id": job_id,
                "chat_id": chat_id,
                "username": username,
                "from_station": state["from_station"],
                "to_station": state["to_station"],
                "journey_date": state["journey_date"],
                "seat_class": state["seat_class"],
                "is_private": is_priv,
                "status": "starting",
            }).execute()

            # 2. Prepare credentials
            phone = state.get("phone", os.getenv("RAILWAY_PHONE"))
            password = state.get("password", os.getenv("RAILWAY_PASSWORD"))

            send_message(
                chat_id,
                f"🚀 Dispatching { 'Private' if is_priv else 'Shared' } job...\nPlease wait for the cloud engine to start."
            )

            # 3. Dispatch to GitHub
            dispatched = dispatch_github_workflow(
                job_id, chat_id, username,
                state["from_station"], state["to_station"],
                state["journey_date"], state["seat_class"],
                phone, password, state.get("desired_trains", "ALL")
            )

            if dispatched:
                # 4. Success Flow
                if is_priv:
                    send_message(
                        chat_id,
                        "🔒 Private session dispatched. Your credentials have been purged from our database."
                    )
                
                # Send the final confirmation message
                send_message(
                    chat_id,
                    "✅ Monitor started!\n\n"
                    f"Job ID:\n`{job_id}`\n\n"
                    f"🚆 {state['from_station']} → {state['to_station']}\n"
                    f"📅 {state['journey_date']}\n"
                    f"💺 {state['class_display']}\n\n"
                    "I'll notify you here the moment tickets are found.",
                    reply_markup=main_menu()
                )
            else:
                # 5. Failure Flow
                supabase.table("monitoring_jobs").update({"status": "failed"}).eq("id", job_id).execute()
                send_message(
                    chat_id, 
                    "❌ Failed to start the cloud engine. Please try again later or contact admin.",
                    reply_markup=main_menu()
                )

            clear_state(chat_id)
            return True

        if text.upper() in ("NO", "N", "CANCEL"):
            clear_state(chat_id)
            send_message(chat_id, "❌ Search cancelled.", reply_markup=main_menu())
            return True

        send_message(chat_id, "Please type YES to start or NO to cancel.")
        return True

    return False


# ============================================================
# COMMANDS
# ============================================================


def handle_start(chat_id, username):

    user = get_verified_user(chat_id)

    if user:
        send_message(
            chat_id,
            "👋 Welcome back!\n\n"
            "Your SUST student account is already verified.\n\n"
            "You can start monitoring Railway tickets.",
            reply_markup=main_menu(),
        )

        return

    set_state(chat_id, "email")

    send_message(
        chat_id,
        "🎓 Railway Ticket Monitor\n\n"
        "This bot is currently restricted to "
        "SUST students.\n\n"
        "Please enter your SUST student email.\n\n"
        "Example:\n"
        "2023331XXX@student.sust.edu",
    )


def handle_email(chat_id, text):

    email = text.strip().lower()

    if not SUST_EMAIL_REGEX.fullmatch(email):
        send_message(
            chat_id,
            "❌ That doesn't look like a valid SUST "
            "student email.\n\n"
            "Please enter an email like:\n"
            "2023331XXX@student.sust.edu",
        )

        return

    send_message(chat_id, "📧 Sending verification code...")

    success = create_verification(chat_id, email)

    if not success:
        send_message(
            chat_id,
            "❌ I couldn't send the verification email.\n\nPlease try again later.",
        )

        return

    set_state(chat_id, "verification", email=email)

    send_message(
        chat_id,
        "📨 Verification code sent!\n\n"
        f"I sent a 6-digit code to:\n{email}\n\n"
        "Enter the code here.\n\n"
        "The code expires in 10 minutes.",
    )


def handle_verification(chat_id, text, username):

    if not text.isdigit() or len(text) != 6:
        send_message(chat_id, "❌ Please enter the 6-digit verification code.")

        return

    success, result = verify_code(chat_id, text, username)

    if success:
        clear_state(chat_id)

        send_message(
            chat_id,
            "✅ Email verified successfully!\n\n"
            f"Student email:\n{result}\n\n"
            "You now have access to the Railway "
            "Ticket Monitor.",
            reply_markup=main_menu(),
        )

        return

    send_message(chat_id, f"❌ {result}")


# ============================================================
# MY SEARCHES
# ============================================================


def show_my_searches(chat_id):

    try:
        result = (
            supabase.table("monitoring_jobs")
            .select("*")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )

        if not result.data:
            send_message(chat_id, "📋 You don't have any monitoring jobs.")

            return

        messages = ["📋 Your recent searches:\n"]

        for job in result.data:
            messages.append(
                f"🚆 {job['from_station']} → "
                f"{job['to_station']}\n"
                f"📅 {job['journey_date']}\n"
                f"💺 {job['seat_class']}\n"
                f"📊 {job['status']}\n"
                f"🆔 {job['id']}\n"
            )

        send_message(chat_id, "\n".join(messages))

    except Exception as e:
        print(f"My searches error: {e}")

        send_message(chat_id, "❌ Could not retrieve your searches.")


# ============================================================
# CANCEL SEARCHES
# ============================================================


def cancel_my_searches(chat_id):

    try:
        result = (
            supabase.table("monitoring_jobs")
            .select("*")
            .eq("chat_id", chat_id)
            .in_("status", ["starting", "queued", "running"])
            .execute()
        )

        if not result.data:
            send_message(chat_id, "There are no active searches.")

            return

        cancelled = 0

        for job in result.data:
            run_id = job.get("github_run_id")

            if run_id:
                cancel_github_run(run_id)

            supabase.table("monitoring_jobs").update(
                {"status": "cancelled", "finished_at": datetime.utcnow().isoformat()}
            ).eq("id", job["id"]).execute()

            cancelled += 1

        send_message(chat_id, f"❌ Cancelled {cancelled} active search(es).")

    except Exception as e:
        print(f"Cancel error: {e}")

        send_message(chat_id, "❌ Could not cancel searches.")


# ============================================================
# STATUS
# ============================================================


def show_status(chat_id):

    user = get_verified_user(chat_id)

    if not user:
        send_message(
            chat_id, "🔒 Not verified.\n\nUse /start to verify your SUST email."
        )

        return

    try:
        result = (
            supabase.table("monitoring_jobs")
            .select("*")
            .eq("chat_id", chat_id)
            .in_("status", ["starting", "queued", "running"])
            .execute()
        )

        active = len(result.data)

        send_message(
            chat_id,
            f"📊 Your status\n\n🎓 SUST email: verified\n🚆 Active searches: {active}",
        )

    except Exception:
        send_message(chat_id, "🎓 Your SUST email is verified.")


# ============================================================
# HELP
# ============================================================


def show_help(chat_id):

    send_message(
        chat_id,
        "ℹ️ Railway Ticket Monitor\n\n"
        "/start — Verify/login\n"
        "/new — Create a new search\n"
        "/mysearches — Show your searches\n"
        "/cancel — Cancel active searches\n"
        "/status — Show your status\n"
        "/help — Show this message\n\n"
        "Class examples:\n"
        "Snigdha\n"
        "S_Chair\n"
        "Snigdha + S_Chair\n"
        "Snigdha + AC_B + S_Chair",
    )


# ============================================================
# ADMIN
# ============================================================


def handle_admin_command(chat_id, text):

    if chat_id != ADMIN_ID:
        return False

    # --------------------------------------------------------
    # /subscribers
    # --------------------------------------------------------

    if text == "/subscribers":
        try:
            result = (
                supabase.table("subscribers")
                .select("username,chat_id,email,verified")
                .execute()
            )

            if not result.data:
                send_message(chat_id, "👥 Subscribers: 0")

                return True

            lines = [f"👥 Subscribers: {len(result.data)}\n"]

            for user in result.data:
                status = "✅" if user.get("verified") else "❌"

                lines.append(
                    f"{status} {user.get('email', 'No email')} ({user['chat_id']})"
                )

            send_message(chat_id, "\n".join(lines))

        except Exception as e:
            send_message(chat_id, f"❌ Error: {e}")

        return True

    # --------------------------------------------------------
    # /remove ID
    # --------------------------------------------------------

    if text.startswith("/remove "):
        parts = text.split()

        if len(parts) < 2:
            send_message(chat_id, "Usage:\n/remove CHAT_ID")

            return True

        rem_id = parts[1]

        try:
            supabase.table("subscribers").delete().eq("chat_id", rem_id).execute()

            send_message(chat_id, f"✅ Removed {rem_id}")

        except Exception as e:
            send_message(chat_id, f"❌ Error: {e}")

        return True

    return False


# ============================================================
# TELEGRAM LISTENER
# ============================================================


def telegram_listener():

    print("🚀 Railway Monitor Telegram backend started...")

    offset = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"

            response = requests.get(
                url, params={"offset": offset, "timeout": 20}, timeout=25
            )

            data = response.json()

            if not data.get("ok"):
                time.sleep(5)

                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message")

                if not message:
                    continue

                if "text" not in message:
                    continue

                chat_id = str(message["chat"]["id"])

                username = message.get("from", {}).get("username", "Unknown")

                text = message["text"].strip()

                state = USER_STATES.get(chat_id)

                # ====================================================
                # VERIFICATION FLOW
                # ====================================================

                if state:
                    if state["step"] == "email":
                        handle_email(chat_id, text)

                        continue

                    if state["step"] == "verification":
                        if text.lower() == "/resend":
                            email = state["email"]

                            if create_verification(chat_id, email):
                                send_message(
                                    chat_id, "📨 A new verification code has been sent."
                                )

                            else:
                                send_message(chat_id, "❌ Could not send a new code.")

                            continue

                        handle_verification(chat_id, text, username)

                        continue

                    # Search flow
                    if process_search_message(chat_id, username, text):
                        continue

                # ====================================================
                # ADMIN
                # ====================================================

                if handle_admin_command(chat_id, text):
                    continue

                # ====================================================
                # COMMANDS
                # ====================================================

                if text == "/start":
                    handle_start(chat_id, username)

                    continue

                if text == "/new":
                    start_new_search(chat_id)

                    continue

                if text == "/mysearches":
                    if get_verified_user(chat_id):
                        show_my_searches(chat_id)

                    else:
                        send_message(
                            chat_id, "🔒 Please verify your SUST student email first."
                        )

                    continue

                if text == "/cancel":
                    cancel_my_searches(chat_id)

                    continue

                if text == "/status":
                    show_status(chat_id)

                    continue

                if text == "/help":
                    show_help(chat_id)

                    continue

                # ====================================================
                # BUTTONS
                # ====================================================

                if text == "🚆 New Search":
                    start_new_search(chat_id)

                    continue

                if text == "📋 My Searches":
                    show_my_searches(chat_id)

                    continue

                if text == "❌ Cancel Search":
                    cancel_my_searches(chat_id)

                    continue

                if text == "ℹ️ Help":
                    show_help(chat_id)

                    continue

                # ====================================================
                # DEFAULT
                # ====================================================

                send_message(
                    chat_id,
                    "I don't understand that command.\n\n"
                    "Use /help to see what I can do.",
                )
        except KeyboardInterrupt:
            print("Telegram listener stopped by user.")
            break        

        except Exception as e:
            print(f"Telegram listener error: {e}")

            time.sleep(20)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    t = threading.Thread(target=run_health_server)
    t.daemon = False 
    t.start()
    print("✅ Health Server started in background...")
    time.sleep(5) 

    telegram_listener()
