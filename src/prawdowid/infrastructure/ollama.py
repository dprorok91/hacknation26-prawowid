"""Bounded local Ollama adapter for structured JSON tasks."""

from typing import Any

import httpx

from prawdowid.application.analyses import AnalysisError, ErrorCode
from prawdowid.infrastructure.model_transport import (
    SYSTEM_MESSAGE,
    StructuredModelTransport,
    assistant_json,
)


class OllamaClient(StructuredModelTransport):
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 20,
        max_concurrency: int = 1,
    ):
        super().__init__(client, timeout_seconds, max_concurrency)
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self._model = model

    async def complete_json(self, *, schema: dict[str, Any], prompt: str) -> dict[str, Any]:
        payload = {
            "model": self._model,
            "format": schema,
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "seed": 0, "num_ctx": 8192, "num_predict": 768},
            "messages": [
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": prompt},
            ],
        }
        usage = self._usage.get()
        tokens_in: object = None
        tokens_out: object = None
        try:
            envelope = await self._post(self._url, payload)
            if envelope.get("done") is not True:
                raise ValueError("Incomplete model response.")
            tokens_in = envelope.get("prompt_eval_count")
            tokens_out = envelope.get("eval_count")
            if envelope.get("done_reason") == "length":
                raise ValueError("Incomplete model response.")
            return assistant_json(envelope.get("message"))
        except (TypeError, ValueError, RecursionError, httpx.HTTPError):
            raise AnalysisError(
                ErrorCode.MODEL_UNAVAILABLE, "The model could not provide a structured result."
            ) from None
        finally:
            if usage is not None:
                usage.record(tokens_in, tokens_out, 0)
