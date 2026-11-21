"""Bound analysis submission bodies before JSON decoding."""

from collections import OrderedDict
from time import monotonic

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_SUBMISSION_BYTES = 256 * 1024
SUBMISSION_PATHS = {"/api/v1/analyses", "/api/v1/adapters/message-demo"}


class SubmissionBodyLimit:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.budgets: OrderedDict[str, tuple[float, int]] = OrderedDict()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") not in SUBMISSION_PATHS
        ):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        peer = client[0] if client else "unknown"
        now = monotonic()
        start, count = self.budgets.pop(peer, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        self.budgets[peer] = start, count + 1
        if len(self.budgets) > 2048:
            self.budgets.popitem(last=False)
        if count >= 120:
            busy = JSONResponse(
                {
                    "detail": {
                        "error_code": "provider_rate_limited",
                        "message": "Too many requests.",
                    }
                },
                status_code=429,
                headers={"Retry-After": str(max(1, int(60 - (now - start))))},
            )
            await busy(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_SUBMISSION_BYTES:
                error = JSONResponse(
                    {
                        "detail": {
                            "error_code": "invalid_input",
                            "message": "Request body is too large.",
                        }
                    },
                    status_code=413,
                )
                await error(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_body() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            result = bytes(body)
            body.clear()
            return {"type": "http.request", "body": result, "more_body": False}

        await self.app(scope, replay_body, send)
