from playwright.sync_api import sync_playwright

BRAVE_PATH = r"C:\Users\USER\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"
PROFILE_PATH = r"C:\Users\USER\RailwayTicketBot\brave-profile"

with sync_playwright() as p:

    print("Starting Brave...")

    context = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE_PATH,
        executable_path=BRAVE_PATH,
        headless=False,
        viewport=None
    )

    page = context.pages[0] if context.pages else context.new_page()

    print("Opening Bangladesh Railway...")

    page.goto(
        "https://eticket.railway.gov.bd/login",
        wait_until="domcontentloaded"
    )

    print("Current URL:", page.url)
    print("Page title:", page.title())

    input("\nPress ENTER to close the browser...")

    context.close()