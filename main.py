import asyncio
import base64
import binascii
import hmac
import ipaddress
import json
import logging
import os
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from websockets.asyncio.client import connect as connect_websocket
from websockets.exceptions import WebSocketException

PROXY_TOKEN = os.environ.get("PROXY_TOKEN")
logger = logging.getLogger(__name__)
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(30.0),
    ) as client:
        app.state.client = client
        app.state.websocket_cookies = {}
        yield


app = FastAPI(title="Python Proxy Gateway", lifespan=lifespan)


async def validate_target_url(
    target_url: str,
    allowed_schemes: frozenset[str] = frozenset({"http", "https"}),
) -> None:
    try:
        parsed = urlsplit(target_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid target URL") from exc

    if (
        parsed.scheme not in allowed_schemes
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise HTTPException(status_code=400, detail="Target must be an HTTP or HTTPS URL")

    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port if port is not None else (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="Target host could not be resolved") from exc

    resolved_ips = {ipaddress.ip_address(address[4][0]) for address in addresses}
    if not resolved_ips or any(not address.is_global for address in resolved_ips):
        raise HTTPException(status_code=403, detail="Target must resolve to public IP addresses")


def remember_websocket_cookies(
    cookie_sessions: dict[str, dict[str, tuple[str, float]]],
    session_id: str,
    hostname: str,
    cookie_header: str,
) -> None:
    now = asyncio.get_running_loop().time()
    for session, domains in list(cookie_sessions.items()):
        live_domains = {
            domain: value
            for domain, value in domains.items()
            if value[1] > now
        }
        if live_domains:
            cookie_sessions[session] = live_domains
        else:
            del cookie_sessions[session]
    if cookie_header:
        cookie_sessions.setdefault(session_id, {})[hostname.lower()] = (
            cookie_header,
            now + 3600,
        )


def get_websocket_cookies(
    cookie_sessions: dict[str, dict[str, tuple[str, float]]],
    session_id: str,
    hostname: str,
) -> str | None:
    session = cookie_sessions.get(session_id, {})
    host = hostname.lower()
    matching_domains = [
        domain
        for domain, (_, expires_at) in session.items()
        if expires_at > asyncio.get_running_loop().time()
        and (host == domain or host.endswith(f".{domain}"))
    ]
    if not matching_domains:
        return None
    domain = max(matching_domains, key=len)
    return session[domain][0]


def get_target_headers(encoded_headers: str | None) -> dict[str, str]:
    if not encoded_headers:
        return {}
    try:
        decoded = base64.urlsafe_b64decode(encoded_headers.encode("ascii"))
        headers = json.loads(decoded)
    except (UnicodeEncodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid target headers") from exc

    if not isinstance(headers, dict) or any(
        not isinstance(name, str) or not isinstance(value, str)
        for name, value in headers.items()
    ):
        raise HTTPException(status_code=400, detail="Invalid target headers")
    return headers


@app.get("/")
async def health() -> dict[str, str]:
    return {
        "status": "Proxy Gateway Active",
        "usage": "Configure playwright_client.py with this gateway URL and PROXY_TOKEN.",
    }


@app.post("/_fetch")
async def fetch_target(request: Request) -> Response:
    if not PROXY_TOKEN:
        raise HTTPException(status_code=503, detail="The gateway PROXY_TOKEN is not configured")

    supplied_token = request.headers.get("authorization", "")
    expected_token = f"Bearer {PROXY_TOKEN}"
    if not hmac.compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=401, detail="Invalid gateway token")

    target_url = request.headers.get("x-proxy-target-url", "")
    await validate_target_url(target_url)
    if urlsplit(target_url).scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="HTTP fetches require an HTTP or HTTPS target")
    method = request.headers.get("x-proxy-target-method", "GET").upper()
    if not method.isalpha():
        raise HTTPException(status_code=400, detail="Invalid target method")

    headers = {
        name.lower(): value
        for name, value in get_target_headers(
            request.headers.get("x-proxy-target-headers")
        ).items()
    }
    connection_tokens = {
        token.strip().lower()
        for token in headers.get("connection", "").split(",")
        if token.strip()
    }
    for name in (*HOP_BY_HOP_HEADERS, *connection_tokens, "host", "content-length"):
        headers.pop(name, None)
    headers["accept-encoding"] = "gzip, deflate"
    session_id = request.headers.get("x-proxy-session-id")
    hostname = urlsplit(target_url).hostname
    if session_id and hostname:
        remember_websocket_cookies(
            request.app.state.websocket_cookies,
            session_id,
            hostname,
            headers.get("cookie", ""),
        )

    try:
        upstream = await request.app.state.client.request(
            method=method,
            url=target_url,
            headers=headers,
            content=await request.body(),
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}") from exc

    response_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in upstream.headers.multi_items()
        if name.lower() not in HOP_BY_HOP_HEADERS
        and name.lower() not in {"content-encoding", "content-length"}
    ]
    response_headers.append((b"content-length", str(len(upstream.content)).encode("ascii")))

    response = Response(content=upstream.content, status_code=upstream.status_code)
    response.raw_headers = response_headers
    return response


@app.websocket("/_ws")
async def websocket_gateway(websocket: WebSocket) -> None:
    if not PROXY_TOKEN or not hmac.compare_digest(
        websocket.cookies.get("_proxy_auth", ""),
        PROXY_TOKEN,
    ):
        await websocket.close(code=4401, reason="Invalid gateway credentials")
        return

    target_url = websocket.query_params.get("url", "")
    try:
        await validate_target_url(
            target_url,
            allowed_schemes=frozenset({"ws", "wss"}),
        )
    except HTTPException as exc:
        await websocket.close(code=4400, reason=str(exc.detail)[:120])
        return

    hostname = urlsplit(target_url).hostname
    session_id = websocket.cookies.get("_proxy_session", "")
    target_cookie = (
        get_websocket_cookies(websocket.app.state.websocket_cookies, session_id, hostname)
        if hostname and session_id
        else None
    )
    extra_headers = {"Cookie": target_cookie} if target_cookie else None
    subprotocols = websocket.scope.get("subprotocols", [])

    try:
        async with connect_websocket(
            target_url,
            additional_headers=extra_headers,
            max_size=16 * 1024 * 1024,
            origin=websocket.headers.get("origin"),
            subprotocols=subprotocols or None,
        ) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)

            async def browser_to_upstream() -> tuple[int, str]:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return message.get("code", 1000), message.get("reason", "")
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def upstream_to_browser() -> tuple[int, str]:
                async for message in upstream:
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)
                return upstream.close_code or 1000, upstream.close_reason or ""

            browser_task = asyncio.create_task(browser_to_upstream())
            upstream_task = asyncio.create_task(upstream_to_browser())
            done, pending = await asyncio.wait(
                {browser_task, upstream_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if browser_task in done:
                close_code, close_reason = browser_task.result()
                if close_code in {1004, 1005, 1006, 1015} or not 1000 <= close_code < 5000:
                    close_code = 1000
                await upstream.close(code=close_code, reason=close_reason)
            else:
                close_code, close_reason = upstream_task.result()
                await websocket.close(code=close_code, reason=close_reason)
    except WebSocketDisconnect:
        return
    except (OSError, WebSocketException, ValueError) as exc:
        logger.warning("WebSocket upstream failed: %s", exc)
        await websocket.close(code=1011, reason="WebSocket upstream failed")
