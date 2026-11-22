"""Optional HTTP Basic gate for limited HTTPS demo access (not multi-user accounts)."""

import base64
import binascii
import secrets

from starlette.responses import Response


class DemoBasicAuth:
    """Deny access unless credentials match; disable by omitting both secrets."""

    def __init__(self, app, *, username: str, password: str):
        if not username.strip() or len(password) < 16:
            raise ValueError("Demo Basic Auth requires a username and a password of >=16 chars")
        self.app = app
        self.username = username.encode("utf-8")
        self.password = password.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            await self.app(scope, receive, send)
            return
        allowed = False
        for key, value in scope.get("headers", []):
            if key.lower() != b"authorization" or not value.startswith(b"Basic "):
                continue
            try:
                decoded = base64.b64decode(value[6:], validate=True)
                supplied_user, supplied_password = decoded.split(b":", maxsplit=1)
                allowed = secrets.compare_digest(supplied_user, self.username) and (
                    secrets.compare_digest(supplied_password, self.password)
                )
            except (ValueError, binascii.Error):
                allowed = False
            break
        if allowed:
            await self.app(scope, receive, send)
            return
        response = Response(status_code=401, headers={
            "WWW-Authenticate": 'Basic realm="PRAWDOWID Demo", charset="UTF-8"',
            "Cache-Control": "no-store",
        })
        await response(scope, receive, send)
