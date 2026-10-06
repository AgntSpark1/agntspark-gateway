"""The chat model behind every Studio assistant, on the platform's own key.

``ChatModel`` is the seam: routes depend on ``get_chat_model``, and tests
override it with a fake, so no test ever calls the real API.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import anthropic
import structlog

from ..config import settings
from ..exceptions import StudioModelError, StudioNotConfiguredError

log = structlog.get_logger(__name__)

# Shown instead of a reply when the model declines a request.
REFUSAL_REPLY = "Sorry, I can't help with that one."


@dataclass(frozen=True)
class ChatReply:
    text: str
    input_tokens: int
    output_tokens: int


class ChatModel(Protocol):
    async def reply(self, *, system: str, messages: list[dict[str, str]]) -> ChatReply: ...


class AnthropicChatModel:
    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def reply(self, *, system: str, messages: list[dict[str, str]]) -> ChatReply:
        try:
            response = await self._client.beta.messages.create(
                model=settings.studio_model,
                max_tokens=settings.studio_max_output_tokens,
                system=system,
                messages=messages,  # type: ignore[arg-type]
                output_config={"effort": settings.studio_effort},
                # On a policy decline the API retries on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # type: ignore[arg-type]
            )
        except anthropic.APIStatusError as exc:
            log.warning("studio: model request failed", status=exc.status_code, error=exc.message)
            raise StudioModelError() from exc
        except anthropic.APIConnectionError as exc:
            log.warning("studio: model unreachable", error=str(exc))
            raise StudioModelError() from exc

        usage = response.usage
        if response.stop_reason == "refusal":
            text = REFUSAL_REPLY
        else:
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if not text:
                raise StudioModelError()
        return ChatReply(
            text=text, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
        )


@lru_cache(maxsize=1)
def _anthropic_model(api_key: str) -> AnthropicChatModel:
    return AnthropicChatModel(api_key)


def get_chat_model() -> ChatModel:
    """FastAPI dependency: the configured model, or 503 when there's no key."""
    if not settings.studio_anthropic_api_key:
        raise StudioNotConfiguredError()
    return _anthropic_model(settings.studio_anthropic_api_key)


__all__ = ["ChatModel", "ChatReply", "AnthropicChatModel", "REFUSAL_REPLY", "get_chat_model"]
