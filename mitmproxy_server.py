"""
Alternative Forward Intercepting Proxy option.
Run with: mitmdump -s mitmproxy_server.py --listen-port 8080
"""
from mitmproxy import http

class TrafficInterceptor:
    def request(self, flow: http.HTTPFlow) -> None:
        print(f"[OUTGOING REQUEST] {flow.request.method} -> {flow.request.pretty_url}")
        flow.request.headers["X-Processed-By"] = "Python-Custom-Proxy"

    def response(self, flow: http.HTTPFlow) -> None:
        print(f"[INCOMING RESPONSE] Status: {flow.response.status_code} for {flow.request.pretty_url}")

addons = [
    TrafficInterceptor()
]
