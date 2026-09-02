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

# --- USER INPUT SECTION ---

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
# DATABASE & TELEGRAM
# ============================================================

def init_db():
    try:
        supabase.table("subscribers").select("count", count="exact").limit(1).execute()
        print("✅ Connected to Supabase Cloud Database.")
    except Exception as e:
        print(f"⚠️ Supabase Warning: {e}")

def get_all_subscribers():
    try:
        res = supabase.table("subscribers").select("chat_id").execute()
        return [str(row["chat_id"]) for row in res.data]
    except:
        return []

def send_telegram(chat_id, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": str(chat_id), "text": message}, timeout=10)
    except:
        pass

def broadcast_to_all(message):
    subscribers = get_all_subscribers()
    print(f"\nBroadcasting to {len(subscribers)} people...")
    for chat_id in subscribers:
        send_telegram(chat_id, message)

def telegram_listener():
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            response = requests.get(url, params={"offset": offset, "timeout": 20}, timeout=25).json()
            if not response.get("ok"): continue
            for update in response["result"]:
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg: continue
                chat_id = str(msg["chat"]["id"])
                username = msg.get("from", {}).get("username", "Unknown")
                text = msg.get("text", "").strip()

                if text == "/start":
                    supabase.table("subscribers").upsert({"chat_id": chat_id, "username": username}).execute()
                    send_telegram(chat_id, f"✅ Subscribed!\nRoute: {FROM_STATION} → {TO_STATION}\nDate: {JOURNEY_DATE_INPUT}")
                elif text == "/stop":
                    supabase.table("subscribers").delete().eq("chat_id", chat_id).execute()
                    send_telegram(chat_id, "❌ Unsubscribed.")
                elif text == "/subscribers" and chat_id == ADMIN_CHAT_ID:
                    count = len(get_all_subscribers())
                    send_telegram(chat_id, f"👥 Subscribers: {count}")
        except:
            time.sleep(5)


# ============================================================
# BROWSER HELPERS (Playwright + separate CAPTCHA solver subprocess)
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
        try:
            popup.first.wait_for(state="hidden", timeout=5000)
        except:
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
    except:
        return False


def solve_captcha_in_subprocess():
    """
    Launches a subprocess that connects to the already running browser via CDP
    and uses SeleniumBase's sb_cdp to solve the Turnstile CAPTCHA.
    Tries multiple solving methods and prints output for debugging.
    """
    solver_script = f"""
import sys
from seleniumbase import sb_cdp
import time

cdp_url = "{CDP_URL}"

try:
    print("Connecting to browser via CDP...", flush=True)
    sb = sb_cdp.Chrome(cdp_url=cdp_url, headless=True)
    print("Connected successfully.", flush=True)
    try:
        # Wait a bit for page to be ready
        sb.sleep(3)
        print("Attempting to solve CAPTCHA...", flush=True)
        # Try primary method
        try:
            sb.solve_captcha()
            print("solve_captcha() completed.", flush=True)
        except Exception as e:
            print(f"solve_captcha() failed: {{e}}", flush=True)
            # Fallback: try clicking Turnstile checkbox manually via JS?
            print("Trying alternative: uc_gui_click_captcha()", flush=True)
            sb.uc_gui_click_captcha()
            print("uc_gui_click_captcha() completed.", flush=True)
        sb.sleep(2)
        print("CAPTCHA solving routine finished.", flush=True)
    finally:
        sb.driver.quit()
        print("Solver driver quit.", flush=True)
except Exception as e:
    print(f"Internal Solver Error: {{e}}", flush=True)
    sys.exit(1)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", solver_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,  # to get strings instead of bytes
    )
    return proc


def login(page):
    print("Login popup detected.")
    page.locator("#mobile_number").fill(RAILWAY_PHONE)
    page.locator("#trainAppLoginPassword").fill(RAILWAY_PASSWORD)
    print("Credentials filled. Launching CAPTCHA solver subprocess...")

    solver_proc = solve_captcha_in_subprocess()

    # Wait for solver process to complete (max 90 seconds)
    start_time = time.time()
    solver_timeout = 90
    while time.time() - start_time < solver_timeout:
        if solver_proc.poll() is not None:
            break
        time.sleep(1)

    if solver_proc.poll() is None:
        print("Solver process did not finish in time. Terminating.")
        solver_proc.terminate()
        return False

    # Capture solver output for debugging
    out, err = solver_proc.communicate()
    print("Solver process finished.")
    if out:
        print("Solver stdout:")
        print(out)
    if err:
        print("Solver stderr:")
        print(err)

    # Wait for LOGIN button to become enabled (up to 30 additional seconds)
    print("Waiting for LOGIN button to become enabled...")
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
        print("LOGIN button is enabled.")
    except Exception:
        print("LOGIN button still disabled after 30 seconds. Trying fallback methods...")

    # Attempt normal click
    print("Clicking LOGIN...")
    try:
        page.locator("#train-app-login-form button[type='submit']").click(timeout=5000)
    except Exception as e:
        print(f"Normal click failed: {e}")
        # Fallback: JS click
        print("Trying JavaScript click...")
        page.evaluate("""
            () => {
                const btn = document.querySelector('#train-app-login-form button[type="submit"]');
                if (btn) btn.click();
            }
        """)

    # Wait for login modal to disappear
    try:
        page.locator(".login-modal-wrapper").wait_for(state="hidden", timeout=10000)
        print("Login successful.")
        return True
    except:
        print("Login popup still visible.")
        return False


def select_station(page, field_id, station):
    print(f"Selecting {station}...")
    field = page.locator(field_id)
    field.wait_for(state="visible", timeout=20000)
    field.click()
    field.fill("")
    field.press_sequentially(station, delay=80)
    page.wait_for_timeout(300)
    menu = page.locator("ul.ui-autocomplete:visible")
    items = menu.locator("li")
    count = items.count()
    for i in range(count):
        text = items.nth(i).inner_text().strip()
        if text.upper() == station.upper():
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
        "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
        "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
    }
    for _ in range(24):
        m_name = datepicker.locator(".ui-datepicker-month").inner_text().strip()
        y_val = int(datepicker.locator(".ui-datepicker-year").inner_text().strip())
        if y_val == year and month_map.get(m_name) == month:
            break
        btn = ("a.ui-datepicker-next" if (year * 12 + month) > (y_val * 12 + month_map.get(m_name)) else "a.ui-datepicker-prev")
        datepicker.locator(btn).click()
        page.wait_for_timeout(250)
    day_cell = datepicker.locator(f'td[data-handler="selectDay"][data-month="{month - 1}"][data-year="{year}"]').get_by_text(str(day), exact=True)
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
    except:
        return results

    for i in range(widget_count):
        widget = train_widgets.nth(i)
        try:
            train_name = widget.locator(".trip-name h2").inner_text(timeout=2000).strip().upper()
            start_loc = widget.locator(".journey-start .journey-location").inner_text().strip().upper()
            end_loc = widget.locator(".journey-end .journey-location").inner_text().strip().upper()
            if (FROM_STATION.upper() not in start_loc or TO_STATION.upper() not in end_loc):
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
        except:
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

            sys.stdout.write(f"\r[🕒 {time.strftime('%H:%M:%S')}] Monitoring {len(data)} train(s)...   ")
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
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BRAVE_PROFILE = r"C:\Users\USER\RailwayTicketBot\brave-profile"

def main():
    init_db()
    threading.Thread(target=telegram_listener, daemon=True).start()

    # Launch Chrome/Brave with remote debugging
    browser_process = subprocess.Popen([
        CHROME_PATH,                # or BRAVE_PATH if you prefer
        "--remote-debugging-port=9222",
        f"--user-data-dir={BRAVE_PROFILE}",
        "--remote-allow-origins=*",
        "--window-size=1920,1080",
        #"--headless=new",         # optional: run without visible window
    ])
    print("Starting browser...")
    time.sleep(5)  # give it time to open

    with sync_playwright() as p:
        try:
            # Connect Playwright to the running browser via CDP
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()

            page.goto(RAILWAY_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("#dest_from", state="visible", timeout=30000)
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
                page.locator("#choose_class").select_option(TARGET_CLASSES[0])
                page.locator("button:has-text('SEARCH TRAINS')").first.click()

                if login_popup_visible(page):
                    if not login(page):
                        print("Login failed. Stopping bot.")
                        return

                try:
                    page.wait_for_selector(".single-trip-wrapper", timeout=40000)
                except:
                    print("No trains found or page took too long.")

                monitor_loop(page)
        except Exception as e:
            print(f"Main Error: {e}")
        finally:
            browser_process.terminate()


if __name__ == "__main__":
    main()