"""OpenRouter adapter for approved public evidence tasks."""

import logging
from typing import Any

import httpx
from pydantic import SecretStr

from prawdowid.application.analyses import AnalysisError, ErrorCode
from prawdowid.infrastructure.model_transport import (
    SYSTEM_MESSAGE,
    StructuredModelTransport,
    assistant_json,
)

_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
logger = logging.getLogger(__name__)


def _strict_schema(node: dict[str, Any]) -> dict[str, Any]:
    """Keep the provider's strict structural subset; Pydantic checks application constraints."""
    schema = {key: node[key] for key in ("type", "enum", "$ref") if key in node}
    for key in ("properties", "$defs"):
        if key in node:
            schema[key] = {name: _strict_schema(item) for name, item in node[key].items()}
    if node.get("type") == "object":
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}))
    if "anyOf" in node:
        schema["anyOf"] = [_strict_schema(item) for item in node["anyOf"]]
    if "items" in node:
        schema["items"] = _strict_schema(node["items"])
    return schema


def _provider_failure(status: object) -> AnalysisError:
    if status == 429:
        return AnalysisError(ErrorCode.PROVIDER_RATE_LIMITED, "The model rate limit was reached.")
    if status in (408, 504):
        return AnalysisError(ErrorCode.TIMEOUT, "The model did not respond in time.")
    return AnalysisError(
        ErrorCode.MODEL_UNAVAILABLE, "The model could not provide a structured result."
    )


class OpenRouterClient(StructuredModelTransport):
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: SecretStr,
        model: str,
        *,
        timeout_seconds: float = 20,
        max_concurrency: int = 1,
    ):
        super().__init__(client, timeout_seconds, max_concurrency)
        self._api_key = api_key
        self._model = model

    async def complete_json(self, *, schema: dict[str, Any], prompt: str) -> dict[str, Any]:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "evidence",
                    "strict": True,
                    "schema": _strict_schema(schema),
                },
            },
            "provider": {"require_parameters": True},
            "temperature": 0,
            "max_tokens": 2048,
            "stream": False,
            "usage": {"include": True},
        }
        counts = {}
        try:
            envelope = await self._post(
                _COMPLETIONS_URL,
                payload,
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
            )
            if isinstance(envelope.get("usage"), dict):
                counts = envelope["usage"]
            if "error" in envelope:
                error = envelope["error"]
                code = error.get("code") if isinstance(error, dict) else None
                # Only bounded numeric identifiers; never log provider messages.
                logger.warning(
                    "OpenRouter provider error: status=%s",
                    code if isinstance(code, int) else "unknown",
                )
                raise _provider_failure(code)
            choices = envelope.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                logger.warning("OpenRouter invalid response: missing_choice")
                raise ValueError("Missing model choice.")
            choice = choices[0]
            if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
                reason = choice.get("finish_reason") if isinstance(choice, dict) else None
                # Never log untrusted provider text or unknown finish reasons.
                label = reason if reason in {"length", "content_filter", "tool_calls"} else "other"
                logger.warning("OpenRouter incomplete response: %s", label)
                raise ValueError("Incomplete model choice.")
            return assistant_json(choice.get("message"))
        except httpx.HTTPStatusError as exc:
            logger.warning("OpenRouter HTTP error: status=%d", exc.response.status_code)
            raise _provider_failure(exc.response.status_code) from None
        except (ValueError, TypeError, RecursionError, httpx.HTTPError) as exc:
            logger.warning("OpenRouter invalid response or transport: %s", type(exc).__name__)
            raise _provider_failure(None) from None
        finally:
            usage = self._usage.get()
            if usage is not None:
                usage.record(
                    counts.get("prompt_tokens"), counts.get("completion_tokens"), counts.get("cost")
                )
