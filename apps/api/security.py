"""Headers every API response carries, however it was reached: through the
app's /api proxy, or at the API's own address.

A pure ASGI middleware, so a streamed file (a filing's PDF, from the bucket)
streams through untouched: only the response's start message is read, to add
headers the handler didn't set itself.
"""
from __future__ import annotations

from starlette.datastructures import MutableHeaders

#: no MIME sniffing of JSON; no framing by other sites (the app frames a
#: filing's PDF from its own origin); no full URLs leaking in Referer
HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
#: only where the API is served over https, which a browser then insists on
HSTS = ("Strict-Transport-Security", "max-age=63072000; includeSubDomains")


class SecurityHeaders:
    def __init__(self, app, *, hsts: bool = False) -> None:
        self.app = app
        self.headers = dict(HEADERS)
        if hsts:
            self.headers[HSTS[0]] = HSTS[1]

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
