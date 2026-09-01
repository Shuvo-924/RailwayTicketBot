from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    print("Connecting to Brave...")

    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")

    print("Connected successfully!")

    contexts = browser.contexts

    for i, context in enumerate(contexts):
        print(f"\nBrowser context {i}")

        for j, page in enumerate(context.pages):
            print(f"  Page {j}")
            print(f"    URL: {page.url}")
            print(f"    Title: {page.title()}")

    input("\nPress ENTER to disconnect...")

    browser.close()