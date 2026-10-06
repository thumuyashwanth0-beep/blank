import asyncio
import base64
import binascii
import hmac
import ipaddress
import json
import os
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

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


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(30.0),
    ) as client:
        app.state.client = client
        yield


app = FastAPI(title="Python Proxy Gateway", lifespan=lifespan)


async def validate_target_url(target_url: str) -> None:
    try:
        parsed = urlsplit(target_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid target URL") from exc

    if (
        parsed.scheme not in {"http", "https"}
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
