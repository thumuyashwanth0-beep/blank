from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from bs4 import BeautifulSoup
import httpx

app = FastAPI(title="Python Proxy Gateway")
client = httpx.AsyncClient(follow_redirects=True)

# Replace with your actual deployed domain
CUSTOM_DOMAIN = "https://blank-bob9.onrender.com"

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"])
async def proxy_gateway(request: Request, path: str, url: str = None):
    # Fallback status check
    if not url:
        return {
            "status": "Proxy Gateway Active",
            "usage": "/?url=https://example.com"
        }

    # Extract headers and sanitize host
    headers = dict(request.headers)
    headers.pop("host", None)

    # Fetch target resource using Python server's IP address
    resp = await client.request(
        method=request.method,
        url=url,
        headers=headers,
        content=await request.body()
    )

    content_type = resp.headers.get("content-type", "")

    # Rewrite HTML links and resources to flow back through custom domain
    if "text/html" in content_type:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag, attr in [("a", "href"), ("img", "src"), ("script", "src"), ("link", "href")]:
            for element in soup.find_all(tag, {attr: True}):
                src = element[attr]
                if src.startswith("http"):
                    element[attr] = f"{CUSTOM_DOMAIN}/?url={src}"
        return HTMLResponse(content=str(soup), status_code=resp.status_code)

    # Stream binary assets (CSS, JS, Images, JSON)
    return StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        headers=dict(resp.headers)
    )
