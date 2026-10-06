from playwright.sync_api import sync_playwright

def run_playwright():
    # Replace with your deployed custom domain or Render URL
    PROXY_DOMAIN = "https://python-proxy-gateway.onrender.com"
    TARGET_SITE = "https://httpbin.org/ip"

    print(f"Launching interactive Playwright browser through {PROXY_DOMAIN}...")

    with sync_playwright() as p:
        # Launch interactive browser
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        # Route request through custom domain proxy server
        full_proxy_url = f"{PROXY_DOMAIN}/?url={TARGET_SITE}"
        print(f"Navigating to: {full_proxy_url}")
        page.goto(full_proxy_url)

        print("\nPage Loaded! Output received from server:")
        print(page.content())

        input("\nPress Enter in console to close browser...")
        browser.close()

if __name__ == "__main__":
    run_playwright()
