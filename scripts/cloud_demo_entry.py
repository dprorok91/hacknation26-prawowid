"""Small HTTPS demo gate wrapping the existing PRAWDOWID ASGI application.

Render terminates HTTPS upstream. Credentials are configured in Render, not in git.
The implementation wraps the existing app without changing its routes or code.
"""

import base64
import binascii
import os
import secrets

from prawdowid.main import app as underlying_app


class AuthenticatedDemo:
    def __init__(self, inner):
        self.inner = inner
        user = os.environ.get("DEMO_BASIC_USER", "")
        password = os.environ.get("DEMO_BASIC_PASSWORD", "")
        if not user.strip() or len(password) < 16:
            raise RuntimeError("Set DEMO_BASIC_USER and DEMO_BASIC_PASSWORD (>=16 chars)")
        self.user = user.encode("utf-8")
        self.password = password.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            await self.inner(scope, receive, send)
            return

        authorized = False
        for key, value in scope.get("headers", []):
            if key.lower() != b"authorization" or not value.lower().startswith(b"basic "):
                continue
            try:
                raw = base64.b64decode(value[6:].strip(), validate=True)
                user, password = raw.split(b":", 1)
                # Check both fields every time to limit timing side channels.
                user_ok = secrets.compare_digest(user, self.user)
                pass_ok = secrets.compare_digest(password, self.password)
                authorized = user_ok and pass_ok
            except (binascii.Error, ValueError):
                authorized = False
            break

        if authorized:
            await self.inner(scope, receive, send)
            return

        headers = [
            (b"www-authenticate", b'Basic realm="PRAWDOWID Demo", charset="UTF-8"'),
            (b"cache-control", b"no-store"),
            (b"content-length", b"0"),
        ]
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": b""})


app = AuthenticatedDemo(underlying_app)
