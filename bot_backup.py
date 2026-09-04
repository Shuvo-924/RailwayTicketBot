import os
import re
import sys
import signal
import platform
import time
import nest_asyncio

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from seleniumbase import SB
from seleniumbase import BaseCase
from supabase import create_client
from datetime import datetime

# Required to bridge the gap between SeleniumBase's internals and Playwright
nest_asyncio.apply()

# ============================================================
# CONFIGURATION & INPUT
# ============================================================

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
RAILWAY_PHONE = os.getenv("RAILWAY_PHONE", "").strip()
RAILWAY_PASSWORD = os.getenv("RAILWAY_PASSWORD", "").strip()

# Job-specific values supplied by GitHub Actions
JOB_ID = os.getenv("JOB_ID", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
USERNAME = os.getenv("USERNAME", "").strip()

SEAT_CLASS_INPUT = os.getenv("SEAT_CLASS", "").strip()

# GitHub automatically provides this variable to every workflow run.
# We use it to associate this process with the monitoring job.
GITHUB_RUN_ID = os.getenv("GITHUB_RUN_ID", "").strip()

# ============================================================
# SUPPORTED CLASSES
# ============================================================

SUPPORTED_CLASSES = {
    "SNIGDHA",
    "S_CHAIR",
    "AC_B",
    "AC_S",
    "F_BERTH",
    "F_SEAT",
    "F_CHAIR",
}

RAILWAY_URL = "https://eticket.railway.gov.bd/login"

DYNAMIC_CHECK_SECONDS = 5
HARD_REFRESH_SECONDS = 60

# Paths
BRAVE_PATH = (
    r"C:\Users\USER\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"
)
BRAVE_PROFILE = r"C:\Users\USER\RailwayTicketBot\brave-profile"

if platform.system().lower() == "linux":
    BRAVE_PROFILE = os.path.join(os.path.expanduser("~"), "railway-bot-profile")

# --- USER INPUT SECTION ---

print("\n" + "=" * 60)
print("             BANGLADESH RAILWAY BOT (STABLE HYBRID)")
print("=" * 60)

if os.getenv("CI"):
    FROM_STATION = os.getenv("FROM_STATION", "").strip()
    TO_STATION = os.getenv("TO_STATION", "").strip()
    JOURNEY_DATE_INPUT = os.getenv("JOURNEY_DATE", "").strip()
    CLASS_CHOICE = os.getenv("CLASS_CHOICE", "3").strip()
else:
    FROM_STATION = input("Enter FROM Station: ").strip()
    TO_STATION = input("Enter TO Station: ").strip()
    JOURNEY_DATE_INPUT = input("Enter Journey Date (DD/MM/YYYY): ").strip()
    print("\nAvailable Class Options:")
    print("  [1] SNIGDHA only")
    print("  [2] S_CHAIR only")
    print("  [3] BOTH (Snigdha & S_Chair)")
    CLASS_CHOICE = input("Select option (1/2/3): ").strip()

journey_date_parts = re.findall(r"\d+", JOURNEY_DATE_INPUT)
if len(journey_date_parts) != 3:
    print(
        f"❌ Invalid journey date format: '{JOURNEY_DATE_INPUT}'. Expected DD/MM/YYYY."
    )
    sys.exit(1)


journey_datetime = datetime.strptime(JOURNEY_DATE_INPUT, "%Y-%m-%d")


def parse_target_classes(value):
    """
    Converts:

        SNIGDHA

    or:

        SNIGDHA|S_CHAIR

    into:

        ["SNIGDHA", "S_CHAIR"]

    Also accepts +, comma and semicolon locally.
    """

    if not value:
        return []

    value = value.strip()

    # GitHub/bot_server format
    if "|" in value:
        raw_classes = value.split("|")

    # Local convenience formats
    else:
        raw_classes = re.split(r"[+,;]", value)

    classes = []

    for item in raw_classes:
        item = item.strip().upper()

        if not item:
            continue

        # Normalize common names
        aliases = {
            "SNIG": "SNIGDHA",
            "SNIGDHA": "SNIGDHA",
            "S CHAIR": "S_CHAIR",
            "SCHAIR": "S_CHAIR",
            "S_CHAIR": "S_CHAIR",
            "AC B": "AC_B",
            "ACB": "AC_B",
            "AC_B": "AC_B",
            "AC S": "AC_S",
            "ACS": "AC_S",
            "AC_S": "AC_S",
            "F BERTH": "F_BERTH",
            "FBERTH": "F_BERTH",
            "F_BERTH": "F_BERTH",
            "F SEAT": "F_SEAT",
            "FSEAT": "F_SEAT",
            "F_SEAT": "F_SEAT",
            "F CHAIR": "F_CHAIR",
            "FCHAIR": "F_CHAIR",
            "F_CHAIR": "F_CHAIR",
        }

        normalized = aliases.get(item, item)

        if normalized in SUPPORTED_CLASSES:
            if normalized not in classes:
                classes.append(normalized)
        else:
            print(f"⚠️ Ignoring unsupported class: {item}")

    return classes


TARGET_CLASSES = parse_target_classes(SEAT_CLASS_INPUT)


def parse_journey_date(value):
    value = value.strip()
    # Try common formats
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"]:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    
    # Fallback: manually detect YYYY-MM-DD vs DD-MM-YYYY
    numbers = re.findall(r"\d+", value)
    if len(numbers) != 3:
        raise ValueError(f"Invalid date: {value}")
    
    if len(numbers[0]) == 4: # YYYY-MM-DD
        return datetime(int(numbers[0]), int(numbers[1]), int(numbers[2]))
    else: # DD-MM-YYYY
        return datetime(int(numbers[2]), int(numbers[1]), int(numbers[0]))


journey_datetime = None

print("\n" + "-" * 30)
print("TARGET CONFIGURED:")
print(f"  Route:   {FROM_STATION} -> {TO_STATION}")
print(f"  Date:    {JOURNEY_DATE_INPUT}")
print(f"  Classes: {', '.join(TARGET_CLASSES)}")
print("-" * 30)


# ============================================================
# DATABASE & TELEGRAM
# ============================================================


def init_db():
    try:
        supabase.table("monitoring_jobs").select("id").limit(1).execute()

        print("✅ Connected to Supabase.")

    except Exception as e:
        print(f"⚠️ Supabase connection warning: {e}")


def update_job_status(status, extra=None):
    """
    Updates the monitoring_jobs row associated with this GitHub job.
    """

    if not JOB_ID:
        return

    payload = {"status": status}

    if status in {"completed", "failed", "cancelled"}:
        payload["finished_at"] = datetime.utcnow().isoformat()

    if extra:
        payload.update(extra)

    for attempt in range(3):
        try:
            supabase.table("monitoring_jobs").update(payload).eq("id", JOB_ID).execute()

            print(f"✅ Job status updated: {status}")
            return

        except Exception as e:
            print(f"⚠️ Failed to update job status (attempt {attempt + 1}/3): {e}")

            time.sleep(1)


def register_github_run():
    """
    GitHub Actions automatically exposes GITHUB_RUN_ID.
    Store it in Supabase so /cancel can identify the exact run.
    """

    if not JOB_ID:
        return

    if not GITHUB_RUN_ID:
        print("⚠️ GITHUB_RUN_ID not available.")
        return

    try:
        run_id = int(GITHUB_RUN_ID)

        supabase.table("monitoring_jobs").update(
            {"github_run_id": run_id, "status": "running"}
        ).eq("id", JOB_ID).execute()

        print(f"✅ Registered GitHub run: {run_id}")

    except Exception as e:
        print(f"⚠️ Could not register GitHub run: {e}")


def get_all_subscribers():
    for attempt in range(3):
        try:
            res = supabase.table("subscribers").select("chat_id").execute()
            if res.data is not None:
                return [str(row["chat_id"]) for row in res.data]
        except Exception:
            time.sleep(1)
    return []


def send_telegram(chat_id, message):
    """
    Sends a Telegram message to exactly one user.

    IMPORTANT:
    This no longer broadcasts to every subscriber.
    """

    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ Telegram bot token missing.")
        return False

    if not chat_id:
        print("⚠️ Telegram chat ID missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        response = requests.post(
            url, data={"chat_id": str(chat_id), "text": message}, timeout=15
        )

        if response.ok:
            return True

        print(f"⚠️ Telegram returned HTTP {response.status_code}: {response.text}")

    except Exception as e:
        print(f"⚠️ Telegram error: {e}")

    return False


def notify_user(message):
    """
    Send notification to the owner of this monitoring job.
    """

    if CHAT_ID:
        return send_telegram(CHAT_ID, message)

    print("⚠️ CHAT_ID unavailable. Notification not sent.")
    return False


def broadcast_to_all(message):
    subscribers = get_all_subscribers()
    print(f"\nBroadcasting to {len(subscribers)} people...")
    for chat_id in subscribers:
        send_telegram(chat_id, message)


# ============================================================
# PLAYWRIGHT HELPERS
# ============================================================


def dismiss_disclaimer(page):
    try:
        popup = page.locator(".disclaimer-bottom-sheet")
        if popup.count() == 0:
            return False
        if not popup.first.is_visible(timeout=1000):
            return False
        btn = popup.first.locator("button.agree-btn")
        if btn.count() == 0:
            return False
        print("Disclaimer popup detected.")
        btn.first.click(timeout=5000)
        page.wait_for_timeout(500)
        print("Disclaimer dismissed.")
        return True
    except:
        return False


def select_station(page, field_id, station):
    print(f"Selecting {station}...")
    field = page.locator(field_id)
    field.wait_for(state="visible", timeout=30000)

    for attempt in range(3):
        field.click()
        field.fill("")
        field.press_sequentially(station, delay=100)
        try:
            menu_item = page.locator("ul.ui-autocomplete:visible li").first
            menu_item.wait_for(state="visible", timeout=5000)
            items = page.locator("ul.ui-autocomplete:visible li")
            suggestions = items.all_inner_texts()
            for i, text in enumerate(suggestions):
                if text.strip().upper() == station.upper():
                    items.nth(i).click()
                    return True
        except:
            page.wait_for_timeout(1000)
    return False


def select_date(page, date_string):
    print(f"Selecting date: {date_string}")
    
    # Use robust parsing to get a datetime object
    try:
        dt = parse_journey_date(date_string)
    except Exception as e:
        print(f"Error parsing date in select_date: {e}")
        return False

    day = dt.day
    month = dt.month
    year = dt.year

    page.locator("#doj").click()
    page.wait_for_timeout(500)
    datepicker = page.locator("#ui-datepicker-div")
    
    month_map = {
        "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
        "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
    }

    # Navigate to the correct Month/Year
    for _ in range(24):
        m_name = datepicker.locator(".ui-datepicker-month").inner_text().strip()
        y_val = int(datepicker.locator(".ui-datepicker-year").inner_text().strip())
        
        if y_val == year and month_map[m_name] == month:
            break
            
        # Click Next if target is in the future, Prev if in the past
        btn = "a.ui-datepicker-next" if (year * 12 + month) > (y_val * 12 + month_map[m_name]) else "a.ui-datepicker-prev"
        datepicker.locator(btn).click()
        page.wait_for_timeout(300)

    # Correctly find the day cell using the exact Day and Month index (0-based)
    day_cell = datepicker.locator(
        f'td[data-handler="selectDay"][data-month="{month - 1}"][data-year="{year}"]'
    ).get_by_text(str(day), exact=True)
    
    day_cell.click()
    return True


def get_seats_from_page(page):
    results = {}
    train_widgets = page.locator(".single-trip-wrapper")
    try:
        widget_count = train_widgets.count()
        for i in range(widget_count):
            w = train_widgets.nth(i)
            train_name = (
                w.locator(".trip-name h2").inner_text(timeout=2000).strip().upper()
            )
            class_results = {}
            seat_blocks = w.locator(".single-seat-class")
            for j in range(seat_blocks.count()):
                block = seat_blocks.nth(j)
                c_name = block.locator(".seat-class-name").inner_text().strip().upper()
                if c_name in TARGET_CLASSES:
                    avail_text = block.locator(".all-seats").inner_text().strip()
                    match = re.search(r"\d+", avail_text)
                    class_results[c_name] = int(match.group()) if match else 0
            if class_results:
                results[train_name] = class_results
    except:
        pass
    return results


def build_ticket_message(train, class_name, count):
    return (
        "🚨🚨 TICKET AVAILABLE! 🚨🚨\n\n"
        f"🚆 Train: {train}\n"
        f"💺 Class: {class_name}\n"
        f"🎟 Seats: {count}\n\n"
        f"📍 {FROM_STATION} → {TO_STATION}\n"
        f"📅 {JOURNEY_DATE_INPUT}\n\n"
        "⚡ Bangladesh Railway e-ticket\n"
        "https://eticket.railway.gov.bd/"
    )


# ============================================================
# MONITORING LOOP
# ============================================================

STOP_REQUESTED = False


def handle_shutdown_signal(signum, frame):
    global STOP_REQUESTED

    print(f"\n⚠️ Shutdown signal received: {signum}")

    STOP_REQUESTED = True


signal.signal(signal.SIGTERM, handle_shutdown_signal)

signal.signal(signal.SIGINT, handle_shutdown_signal)


def monitor_loop(page):
    print("\n" + "=" * 60)
    print("             MONITORING ACTIVE")
    print("=" * 60)

    print(f"Route:   {FROM_STATION} → {TO_STATION}")
    print(f"Date:    {JOURNEY_DATE_INPUT}")
    print(f"Classes: {', '.join(TARGET_CLASSES)}")

    if JOB_ID:
        print(f"Job ID:  {JOB_ID}")

    if GITHUB_RUN_ID:
        print(f"Run ID:  {GITHUB_RUN_ID}")

    print("=" * 60)

    previous_state = {}

    refresh_start = time.time()
    FOUND = False

    while True:
        if STOP_REQUESTED:
            print("\n🛑 Monitoring cancelled.")

            update_job_status("cancelled")

            return
        if FOUND:
            print("\n🛑 Desired ticket found. Monitoring stopped.")

            update_job_status("completed")

            return

        try:
            data = get_seats_from_page(page)

            for train, classes in data.items():
                for class_name, count in classes.items():
                    key = f"{train}|{class_name}"

                    previous_count = previous_state.get(key, 0)

                    # Notify when:
                    # 0 -> available
                    # OR
                    # available count changes
                    if count > 0 and class_name in TARGET_CLASSES:
                        print(f"\n🚨 [FOUND] {train} - {class_name}: {count} seats")
                        message = build_ticket_message(train, class_name, count)
                        notify_user(message)
                        print("Desired ticket found. Stopping monitoring.")
                        FOUND = True
                        update_job_status("completed", {"found": True})
                                            
                    if count > 0 and count != previous_count and FOUND is False:
                        print(f"\n🚨 [FOUND] {train} - {class_name}: {count} seats")

                        message = build_ticket_message(train, class_name, count)

                        notify_user(message)

                    previous_state[key] = count
                    

            current_time = time.strftime("%H:%M:%S")

            sys.stdout.write(
                f"\r[🕒 {current_time}] Monitoring {len(data)} train(s)...   "
            )

            sys.stdout.flush()

            # ------------------------------------------------
            # HARD REFRESH
            # ------------------------------------------------

            if time.time() - refresh_start >= HARD_REFRESH_SECONDS:
                print("\n\n🔄 Reloading page...")

                page.reload(wait_until="domcontentloaded", timeout=60000)

                page.wait_for_timeout(5000)

                dismiss_disclaimer(page)

                refresh_start = time.time()

            # ------------------------------------------------
            # JOURNEY DATE CHECK
            # ------------------------------------------------

            if datetime.now() > journey_datetime:
                print("\n\n📅 Journey date has passed.")

                print("🏁 Monitoring completed.")

                update_job_status("completed")

                return

            time.sleep(DYNAMIC_CHECK_SECONDS)

        except KeyboardInterrupt:
            print("\n🛑 Monitoring stopped.")

            update_job_status("cancelled")

            return

        except Exception as e:
            print(f"\n⚠️ Monitoring error: {e}")

            time.sleep(DYNAMIC_CHECK_SECONDS)


# ============================================================
# MAIN HYBRID EXECUTION
# ============================================================


def main():
    global journey_datetime

    print("\n" + "=" * 60)
    print("       BANGLADESH RAILWAY TICKET MONITOR")
    print("=" * 60)

    journey_datetime = parse_journey_date(JOURNEY_DATE_INPUT)

    # --------------------------------------------------------
    # Configuration display
    # --------------------------------------------------------

    print("\nTARGET CONFIGURATION")
    print("-" * 40)

    print(f"Route:    {FROM_STATION} → {TO_STATION}")

    print(f"Date:     {JOURNEY_DATE_INPUT}")

    print(f"Classes:  {', '.join(TARGET_CLASSES)}")

    if JOB_ID:
        print(f"Job ID:   {JOB_ID}")

    if CHAT_ID:
        print(f"Chat ID:  {CHAT_ID}")

    if GITHUB_RUN_ID:
        print(f"Run ID:   {GITHUB_RUN_ID}")

    print("-" * 40)

    # --------------------------------------------------------
    # Supabase
    # --------------------------------------------------------

    init_db()
    register_github_run()
    USER_PHONE = os.getenv("user_phone") or os.getenv("RAILWAY_PHONE")
    USER_PASS = os.getenv("user_pass") or os.getenv("RAILWAY_PASSWORD")

    with SB(uc=True, xvfb=True, locale="en") as sb:
        try:
            print("🚀 Step 1: SeleniumBase launching undetected browser...")
            # Navigate directly to the login page
            sb.uc_open("https://eticket.railway.gov.bd/login")
            sb.sleep(2)

            # Dismiss any disclaimer if present (usually on main page, not login)
            if sb.is_element_visible(".disclaimer-bottom-sheet"):
                sb.click("button.agree-btn")
                sb.sleep(1)

            print("Filling credentials...")
            sb.wait_for_element_visible("#mobile_number", timeout=10)
            sb.type("#mobile_number", USER_PHONE)
            sb.type("#password", USER_PASS)  # correct password field id

            print("🧩 Solving CAPTCHA...")
            sb.uc_gui_click_captcha()
            sb.sleep(2)

            # Wait for the login button to become enabled
            sb.wait_for_element_visible(
                ".login-form-submit-btn:not([disabled])", timeout=30
            )
            print("Login button enabled. Clicking...")
            sb.click(".login-form-submit-btn")
            sb.sleep(5)
            debug_port = sb.driver.capabilities["goog:chromeOptions"]["debuggerAddress"]
            print(f"🔗 Raw Chrome Debugger Address found: {debug_port}")

            print(f"🔗 CDP Bridge established: {debug_port}")
        except Exception as e:
            print(f"Error in SeleniumBase phase: {e}")
            return

        # Playwright phase
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(f"http://{debug_port}")
                context = browser.contexts[0]
                page = context.pages[0]
                # Ensure we are on the main page after login
                page.goto(
                    "https://eticket.railway.gov.bd/", wait_until="domcontentloaded"
                )
                page.wait_for_timeout(2000)
                dismiss_disclaimer(page)

                if (
                    select_station(page, "#dest_from", FROM_STATION)
                    and select_station(page, "#dest_to", TO_STATION)
                    and select_date(page, JOURNEY_DATE_INPUT)
                ):
                    page.locator("#choose_class").select_option(TARGET_CLASSES[0])
                    page.locator("button:has-text('SEARCH TRAINS')").first.click()
                    try:
                        page.wait_for_selector(".single-trip-wrapper", timeout=90000)
                    except:
                        print("Waiting for results widgets...")
                    monitor_loop(page)
                else:
                    print("Failed to fill search form properly.")
            except KeyboardInterrupt:
                print("\n🛑 Program interrupted.")

                update_job_status("cancelled")

            except Exception as e:
                print(f"\n❌ Fatal error: {e}")

                update_job_status("failed")

                # Notify the user
                notify_user(
                    "❌ Your railway ticket monitor stopped "
                    "because of an error.\n\n"
                    f"Error: {e}"
                )

                raise

            finally:
                print("\n🏁 Railway monitor finished.")


if __name__ == "__main__":
    main()
