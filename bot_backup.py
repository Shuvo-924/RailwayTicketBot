import os
import re
import sys
import threading
import subprocess
import time

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from supabase import create_client


# ============================================================
# CONFIGURATION & INPUT
# ============================================================

load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
RAILWAY_PHONE = os.getenv("RAILWAY_PHONE", "").strip()
RAILWAY_PASSWORD = os.getenv("RAILWAY_PASSWORD", "").strip()

CDP_URL = "http://127.0.0.1:9222"
RAILWAY_URL = "https://eticket.railway.gov.bd/"

DYNAMIC_CHECK_SECONDS = 5
HARD_REFRESH_SECONDS = 60

# ------------------------------------------------------------
# USER INPUT SECTION
# ------------------------------------------------------------

print("\n" + "=" * 60)
print("             BANGLADESH RAILWAY BOT")
print("=" * 60)

FROM_STATION = input("Enter FROM Station: ").strip()
TO_STATION = input("Enter TO Station: ").strip()
JOURNEY_DATE_INPUT = input("Enter Journey Date (DD/MM/YYYY): ").strip()

print("\nAvailable Class Options:")
print("  [1] SNIGDHA only")
print("  [2] S_CHAIR only")
print("  [3] BOTH (Snigdha & S_Chair)")
class_choice = input("Select option (1/2/3): ").strip()

if class_choice == "1":
    TARGET_CLASSES = ["SNIGDHA"]
elif class_choice == "2":
    TARGET_CLASSES = ["S_CHAIR"]
else:
    TARGET_CLASSES = ["SNIGDHA", "S_CHAIR"]

print("\n" + "-" * 30)
print("TARGET CONFIGURED:")
print(f"  Route:   {FROM_STATION} -> {TO_STATION}")
print(f"  Date:    {JOURNEY_DATE_INPUT}")
print(f"  Classes: {', '.join(TARGET_CLASSES)}")
print("-" * 30)


# ============================================================
# VALIDATE CONFIG
# ============================================================

if not TELEGRAM_BOT_TOKEN:
    print("\nERROR: TELEGRAM_BOT_TOKEN not found in .env")
    raise SystemExit(1)

if not ADMIN_CHAT_ID:
    print("\nERROR: TELEGRAM_CHAT_ID not found in .env")
    raise SystemExit(1)


# ============================================================
# DATABASE
# ============================================================


def init_db():
    """Verifies connection to Supabase."""
    try:
        # Just a simple ping to see if we can reach the table
        supabase.table("subscribers").select("count", count="exact").limit(1).execute()
        print("✅ Connected to Supabase Cloud Database.")
    except Exception as e:
        print(f"⚠️ Supabase Connection Warning: {e}")


def add_subscriber(chat_id, username):
    """Adds or updates a subscriber in the Supabase cloud table."""
    try:
        supabase.table("subscribers").upsert(
            {"chat_id": str(chat_id), "username": str(username)}
        ).execute()
        return True
    except Exception as e:
        print(f"Supabase Add Error: {e}")
        return False


def remove_subscriber(chat_id):
    """Deletes a subscriber from the Supabase cloud table."""
    try:
        supabase.table("subscribers").delete().eq("chat_id", str(chat_id)).execute()
        return True
    except Exception as e:
        print(f"Supabase Remove Error: {e}")
        return False


def get_all_subscribers():
    """Fetches all subscriber chat IDs from the cloud database."""
    try:
        res = supabase.table("subscribers").select("chat_id").execute()
        return [str(row["chat_id"]) for row in res.data]
    except Exception as e:
        print(f"Supabase Fetch Error: {e}")
        return []


# ============================================================
# TELEGRAM
# ============================================================


def send_telegram(chat_id, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url, data={"chat_id": str(chat_id), "text": message}, timeout=10
        )
        return response.status_code == 200
    except Exception:
        return False


def broadcast_to_all(message):
    subscribers = get_all_subscribers()
    print(f"Broadcasting to {len(subscribers)} people...")
    for chat_id in subscribers:
        send_telegram(chat_id, message)


def telegram_listener():
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            response = requests.get(
                url, params={"offset": offset, "timeout": 20}, timeout=25
            )
            data = response.json()
            if not data.get("ok"):
                time.sleep(5)
                continue
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg:
                    continue
                chat_id = str(msg["chat"]["id"])
                username = msg.get("from", {}).get("username", "Unknown")
                text = msg.get("text", "").strip()

                if text == "/start":
                    add_subscriber(chat_id, username)
                    send_telegram(
                        chat_id,
                        f"✅ Subscribed!\nRoute: {FROM_STATION} → {TO_STATION}\nDate: {JOURNEY_DATE_INPUT}\nClasses: {', '.join(TARGET_CLASSES)}",
                    )
                elif text == "/stop":
                    remove_subscriber(chat_id)
                    send_telegram(chat_id, "❌ Unsubscribed.")
                elif text == "/subscribers" and chat_id == ADMIN_CHAT_ID:
                    count = len(get_all_subscribers())
                    send_telegram(chat_id, f"👥 Subscribers: {count}")
        except Exception:
            time.sleep(5)


# ============================================================
# BROWSER HELPERS
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
            print("Disclaimer found, but I AGREE button was not found.")
            return False

        print("Disclaimer popup detected.")

        btn.first.click(timeout=5000)

        # Wait for Angular to remove/hide it
        try:
            popup.first.wait_for(state="hidden", timeout=5000)
        except Exception:
            # It may be removed from the DOM rather than hidden
            pass

        page.wait_for_timeout(500)

        print("Disclaimer dismissed.")
        return True

    except Exception as e:
        print(f"Error dismissing disclaimer: {e}")
        return False


def login_popup_visible(page):
    try:
        return page.locator(".login-modal-wrapper").is_visible(timeout=2000)
    except Exception:
        return False


def login(page):
    print("Login popup detected.")

    page.locator("#mobile_number").fill(RAILWAY_PHONE)
    page.locator("#trainAppLoginPassword").fill(RAILWAY_PASSWORD)

    login_btn = page.locator("#train-app-login-form button[type='submit']")

    # Wait for the form to become valid/enabled.
    try:
        login_btn.wait_for(state="visible", timeout=5000)
    except Exception:
        print("Login button not found.")
        return False

    print("Credentials filled.")

    # Turnstile must be completed manually if it appears.
    if page.locator("input[name='cf-turnstile-response']").count() > 0:
        print("Please complete the Cloudflare verification if required.")

        try:
            page.wait_for_function(
                """
                () => {
                    const el = document.querySelector(
                        'input[name="cf-turnstile-response"]'
                    );
                    return el && el.value && el.value.length > 0;
                }
                """,
                timeout=120000,
            )
            print("Cloudflare verification detected.")
        except Exception:
            print("Verification was not completed in time.")
            return False

    # Wait until Angular enables LOGIN.
    try:
        page.wait_for_function(
            """
            () => {
                const btn = document.querySelector(
                    '#train-app-login-form button[type="submit"]'
                );
                return btn && !btn.disabled;
            }
            """,
            timeout=30000,
        )
    except Exception:
        print("LOGIN button is still disabled.")
        return False

    print("Clicking LOGIN...")

    login_btn.click()

    # Give Angular time to process the login.
    page.wait_for_timeout(3000)

    # Check whether login popup disappeared.
    try:
        page.locator(".login-modal-wrapper").wait_for(state="hidden", timeout=10000)
        print("Login successful.")
        return True
    except Exception:
        print("Login popup is still visible.")

        # Don't assume failure immediately; print useful information.
        try:
            error = page.locator(".login-modal-wrapper").inner_text(timeout=2000)

            print("Login modal text:")
            print(error[:1000])
        except Exception:
            pass

        return False


def select_station(page, field_id, station):
    print(f"Selecting {station}...")
    field = page.locator(field_id)
    field.wait_for(state="visible", timeout=10000)
    field.click()
    field.fill("")
    field.press_sequentially(station, delay=80)
    page.wait_for_timeout(1500)
    menu = page.locator("ul.ui-autocomplete:visible")
    items = menu.locator("li")
    suggestions = items.all_inner_texts()
    for i, text in enumerate(suggestions):
        if text.strip().upper() == station.upper():
            items.nth(i).click()
            return True
    return False


def select_date(page, date_string):
    print(f"Selecting date: {date_string}")
    nums = re.findall(r"\d+", date_string)
    day, month, year = int(nums[0]), int(nums[1]), int(nums[2])
    if year < 100:
        year += 2000
    page.locator("#doj").click()
    page.wait_for_timeout(500)
    datepicker = page.locator("#ui-datepicker-div:visible")
    month_map = {
        "January": 1,
        "February": 2,
        "March": 3,
        "April": 4,
        "May": 5,
        "June": 6,
        "July": 7,
        "August": 8,
        "September": 9,
        "October": 10,
        "November": 11,
        "December": 12,
    }
    for _ in range(24):
        m_name = datepicker.locator(".ui-datepicker-month").inner_text().strip()
        y_val = int(datepicker.locator(".ui-datepicker-year").inner_text().strip())
        if y_val == year and month_map.get(m_name) == month:
            break
        btn = (
            "a.ui-datepicker-next"
            if (year * 12 + month) > (y_val * 12 + month_map.get(m_name))
            else "a.ui-datepicker-prev"
        )
        datepicker.locator(btn).click()
        page.wait_for_timeout(250)
    day_cell = datepicker.locator(
        f'td[data-handler="selectDay"][data-month="{month - 1}"][data-year="{year}"]'
    ).get_by_text(str(day), exact=True)
    day_cell.click()
    return True


# ============================================================
# PARSING LOGIC
# ============================================================


def get_seats_from_page(page):
    results = {}
    train_widgets = page.locator(".single-trip-wrapper")

    try:
        widget_count = train_widgets.count()
    except Exception:
        return results

    for i in range(widget_count):
        widget = train_widgets.nth(i)
        try:
            train_name = (
                widget.locator(".trip-name h2").inner_text(timeout=2000).strip().upper()
            )

            # Verify route locations
            start_loc = (
                widget.locator(".journey-start .journey-location")
                .inner_text()
                .strip()
                .upper()
            )
            end_loc = (
                widget.locator(".journey-end .journey-location")
                .inner_text()
                .strip()
                .upper()
            )

            if (
                FROM_STATION.upper() not in start_loc
                or TO_STATION.upper() not in end_loc
            ):
                continue

            class_results = {}
            seat_blocks = widget.locator(".single-seat-class")
            for j in range(seat_blocks.count()):
                block = seat_blocks.nth(j)
                c_name = block.locator(".seat-class-name").inner_text().strip().upper()

                if c_name in TARGET_CLASSES:
                    avail_text = block.locator(".all-seats").inner_text().strip()
                    match = re.search(r"\d+", avail_text)
                    count = int(match.group()) if match else 0
                    class_results[c_name] = count

            if class_results:
                results[train_name] = class_results
        except Exception:
            continue
    return results


# ============================================================
# MONITORING
# ============================================================


def monitor_loop(page):
    print("\n" + "=" * 60)
    print("             MONITORING ACTIVE")
    print("=" * 60)

    previous_state = {}
    refresh_start = time.time()

    while True:
        try:
            data = get_seats_from_page(page)

            for train, classes in data.items():
                for class_name, count in classes.items():
                    key = f"{train}|{class_name}"
                    old_count = previous_state.get(key)

                    if count > 0 and count != old_count:
                        print(f"\n[🚨 FOUND] {train} - {class_name}: {count} seats")
                        msg = (
                            "🚨🚨 TICKET AVAILABLE! 🚨🚨\n\n"
                            f"🚆 Train: {train}\n"
                            f"💺 Class: {class_name}\n"
                            f"🎟 Seats: {count}\n\n"
                            f"📍 {FROM_STATION} → {TO_STATION}\n"
                            f"📅 {JOURNEY_DATE_INPUT}\n\n"
                            "⚡ eticket.railway.gov.bd"
                        )
                        broadcast_to_all(msg)
                    previous_state[key] = count

            sys.stdout.write(
                f"\r[🕒 {time.strftime('%H:%M:%S')}] Monitoring {len(data)} train(s)...   "
            )
            sys.stdout.flush()

            if time.time() - refresh_start >= HARD_REFRESH_SECONDS:
                print("\n\n🔄 RELOADING PAGE...")
                page.reload(wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                dismiss_disclaimer(page)
                refresh_start = time.time()

            time.sleep(DYNAMIC_CHECK_SECONDS)

        except KeyboardInterrupt:
            print("\nStopped.")
            return
        except Exception as e:
            print(f"\nMonitoring error: {e}")
            time.sleep(DYNAMIC_CHECK_SECONDS)


# ============================================================
# MAIN
# ============================================================

BRAVE_PATH = r"C:\Users\USER\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"
BRAVE_PROFILE = r"C:\Users\USER\RailwayTicketBot\brave-profile"

def main():
    init_db()
    threading.Thread(target=telegram_listener, daemon=True).start()

    with sync_playwright() as p:
        try:
            brave_process = subprocess.Popen([
                BRAVE_PATH,
                "--remote-debugging-port=9222",
                f"--user-data-dir={BRAVE_PROFILE}",
            ])

            print("Starting Brave...")
            time.sleep(2)

            # Connect Playwright to Brave through CDP
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()

            page.goto(RAILWAY_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            dismiss_disclaimer(page)
            if login_popup_visible(page):
                if not login(page):
                    print("Login failed. Stopping bot.")
                    return

            if (
                select_station(page, "#dest_from", FROM_STATION)
                and select_station(page, "#dest_to", TO_STATION)
                and select_date(page, JOURNEY_DATE_INPUT)
            ):
                # We always pick a class for the initial search form to proceed
                page.locator("#choose_class").select_option(TARGET_CLASSES[0])
                page.locator("button:has-text('SEARCH TRAINS')").first.click()

                try:
                    page.wait_for_selector(".single-trip-wrapper", timeout=20000)
                except Exception:
                    print("No trains found or page took too long.")

                if login_popup_visible(page):
                    if not login(page):
                        print("Login failed. Stopping bot.")
                        return

                monitor_loop(page)
        except Exception as e:
            print(f"CDP Error: {e}. Ensure Brave is running with port 9222.")


if __name__ == "__main__":
    main()
