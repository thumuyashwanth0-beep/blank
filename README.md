# Python Proxy Gateway

This gateway lets Playwright load HTTP and HTTPS pages without configuring a
browser `CONNECT` proxy. Playwright intercepts each page request and sends it
to the gateway; the gateway fetches the target URL server-side and returns the
response. The browser retains the original page URL, so normal relative links,
JavaScript `fetch`/XHR requests, redirects, and page-origin storage continue to
work without rewriting HTML.

## Configure

Set the same secret on the gateway host and the machine running Playwright:

```text
PROXY_TOKEN=<a-long-random-secret>
```

For the Playwright client, also configure:

```text
PROXY_DOMAIN=https://your-deployed-gateway.example
TARGET_SITE=https://example.com
```

For local development, start the gateway:

```bash
uvicorn main:app --host 127.0.0.1 --port 8080
```

Then run the client with `PROXY_DOMAIN=http://127.0.0.1:8080` and the same
`PROXY_TOKEN`. Install the browser binary if Playwright requests it:

```bash
playwright install chromium
```

If deploying the gateway publicly, configure `PROXY_TOKEN` in the hosting
provider's secret/environment settings. Do not commit the token to source
control.

## Scope and limitations

The gateway accepts arbitrary HTTP/HTTPS targets, but rejects hosts that
resolve to non-public IP addresses. Keep the token private and consider
restricting outbound network access at the hosting provider as an additional
SSRF safeguard. The gateway must be reachable by the Playwright client.

Browser HTTP and HTTPS requests, including page subresources and API calls,
are routed through the gateway. WebSocket connections are blocked so they do
not bypass it; WebRTC and other browser features that bypass normal page
requests are not transparently proxied. Websites may also reject server-side
traffic or depend on browser TLS/network characteristics the gateway cannot
reproduce.
