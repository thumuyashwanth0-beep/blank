import base64
import json
import os
import sys
import time
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Request,
    Route,
    sync_playwright,
)


PROXY_DOMAIN = os.environ.get("PROXY_DOMAIN", "https://blank-bob9.onrender.com")
TARGET_SITE = os.environ.get("TARGET_SITE", "https://httpbin.org/ip")
PROXY_TOKEN = os.environ.get("PROXY_TOKEN")
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def copy_set_cookies(response: httpx.Response, request_url: str, context: BrowserContext) -> None:
    target = urlsplit(request_url)
    cookies = []
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        for name, morsel in parsed.items():
            cookie = {"name": name, "value": morsel.value}
            domain = morsel["domain"].lstrip(".")
            if domain:
                target_host = (target.hostname or "").lower()
                if target_host != domain.lower() and not target_host.endswith(f".{domain.lower()}"):
                    continue
                cookie["domain"] = morsel["domain"]
                cookie["path"] = morsel["path"] or "/"
            else:
                cookie["url"] = f"{target.scheme}://{target.netloc}"

            if morsel["secure"]:
                cookie["secure"] = True
            if morsel["httponly"]:
                cookie["httpOnly"] = True
            if morsel["samesite"]:
                cookie["sameSite"] = morsel["samesite"].capitalize()
            if morsel["max-age"]:
                cookie["expires"] = time.time() + int(morsel["max-age"])
            elif morsel["expires"]:
                cookie["expires"] = parsedate_to_datetime(morsel["expires"]).timestamp()
            cookies.append(cookie)

    if cookies:
        try:
            context.add_cookies(cookies)
        except PlaywrightError as exc:
            print(f"Could not apply cookies from {request_url}: {exc}", file=sys.stderr)


def proxy_request(
    route: Route,
    request: Request,
    *,
    gateway: str,
    token: str,
    client: httpx.Client,
    context: BrowserContext,
) -> None:
    if urlsplit(request.url).scheme not in {"http", "https"}:
        route.continue_()
        return

    target_headers = request.all_headers()
    metadata = base64.urlsafe_b64encode(
        json.dumps(target_headers).encode("utf-8")
    ).decode("ascii")
    try:
        response = client.post(
            f"{gateway}/_fetch",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Proxy-Target-URL": request.url,
                "X-Proxy-Target-Method": request.method,
                "X-Proxy-Target-Headers": metadata,
            },
            content=request.post_data_buffer or b"",
        )
    except httpx.HTTPError as exc:
        print(f"Gateway request failed for {request.url}: {exc}", file=sys.stderr)
        route.abort("failed")
        return

    try:
        copy_set_cookies(response, request.url, context)
    except (ValueError, OverflowError) as exc:
        print(f"Could not apply cookies from {request.url}: {exc}", file=sys.stderr)

    response_headers = {
        name: value
        for name, value in response.headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
        and name.lower() not in {"content-length", "content-encoding", "set-cookie"}
    }
    route.fulfill(
        status=response.status_code,
        headers=response_headers,
        body=response.content,
    )


def run_playwright() -> None:
    if not PROXY_TOKEN:
        raise RuntimeError("Set PROXY_TOKEN to the same secret configured on the gateway.")

    gateway = PROXY_DOMAIN.rstrip("/")
    print(f"Launching Playwright through the HTTPS-fetch gateway at {gateway}...")
    with httpx.Client(timeout=60.0) as client, sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(service_workers="block")
        context.route_web_socket(
            "**/*",
            lambda web_socket: web_socket.close(
                code=1008,
                reason="WebSockets are not relayed by this HTTP gateway.",
            ),
        )
        context.route(
            "**/*",
            lambda route, request: proxy_request(
                route,
                request,
                gateway=gateway,
                token=PROXY_TOKEN,
                client=client,
                context=context,
            ),
        )
        page = context.new_page()
        print(f"Navigating to: {TARGET_SITE}")
        page.goto(TARGET_SITE, wait_until="domcontentloaded")
        print(f"\nPage loaded at {page.url}")
        input("\nPress Enter in console to close browser...")
        browser.close()


if __name__ == "__main__":
    run_playwright()
