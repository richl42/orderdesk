"""The only code that talks to the AI API.

Every call asks for one tool call with a strict JSON schema, and logs tokens and
cost. Answers can be cached by a hash of the whole request, so the accuracy test
gives repeatable results. Callers validate what comes back; this file only
reports whether a tool call arrived.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

MILLION = Decimal(1_000_000)
# Cache writes cost 1.25 times the input price, cache reads 0.1 times.
CACHE_WRITE_FACTOR = Decimal("1.25")
CACHE_READ_FACTOR = Decimal("0.1")


class AIUnavailable(RuntimeError):
    """The AI service could not be used at all (no key, no network, account problem). Worth trying again later."""


@dataclass
class ToolCall:
    input: dict | None  # the tool's arguments; None when no usable call came back
    problem: str | None = None  # why there is no usable call, in plain words
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    cached: bool = False

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_write_tokens + self.cache_read_tokens


class AI(Protocol):
    def call_tool(self, *, system: str, user: str, tool: dict, attempt: int = 1,
                  max_tokens: int = 16000) -> ToolCall: ...


def cost_gbp(call: ToolCall, price_in: Decimal | None, price_out: Decimal | None) -> Decimal | None:
    """What a call cost in pounds, from the per-million-token prices. None when prices are not set."""
    if price_in is None or price_out is None:
        return None
    input_cost = (Decimal(call.input_tokens)
                  + Decimal(call.cache_write_tokens) * CACHE_WRITE_FACTOR
                  + Decimal(call.cache_read_tokens) * CACHE_READ_FACTOR) * price_in
    total = (input_cost + Decimal(call.output_tokens) * price_out) / MILLION
    return total.quantize(Decimal("0.000001"))


def request_key(model: str, system: str, user: str, tool: dict, attempt: int) -> str:
    payload = json.dumps({"model": model, "system": system, "user": user, "tool": tool, "attempt": attempt},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AnthropicAI:
    """Calls Claude through the official SDK."""

    def __init__(self, api_key: str, model: str, cache_dir: Path | None = None, effort: str = "medium",
                 timeout: float = 120.0):
        self.api_key = api_key
        self.model = model
        self.cache_dir = cache_dir
        self.effort = effort
        self.timeout = timeout
        self._client = None

    def _sdk(self):
        if not self.api_key:
            raise AIUnavailable("No AI key is set. Add ANTHROPIC_API_KEY to the .env file and restart.")
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout, max_retries=2)
        return self._client

    def _cache_path(self, key: str) -> Path | None:
        return self.cache_dir / f"{key}.json" if self.cache_dir else None

    def call_tool(self, *, system: str, user: str, tool: dict, attempt: int = 1,
                  max_tokens: int = 16000) -> ToolCall:
        key = request_key(self.model, system, user, tool, attempt)
        path = self._cache_path(key)
        if path and path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            return ToolCall(**{**saved, "cached": True})

        call = self._call(system, user, tool, max_tokens)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({**asdict(call), "cached": False}, ensure_ascii=False), encoding="utf-8")
        return call

    def _call(self, system: str, user: str, tool: dict, max_tokens: int) -> ToolCall:
        import anthropic

        client = self._sdk()
        try:
            # Claude Sonnet 5.5 refuses a fixed temperature and forced tool choice, so the prompt asks for the
            # tool and strict mode keeps its arguments to the schema.
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=[{**tool, "strict": True}],
                tool_choice={"type": "auto"},
                output_config={"effort": self.effort},
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError as exc:
            raise AIUnavailable("The AI key was not accepted. Check ANTHROPIC_API_KEY in the .env file.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIUnavailable("The AI account refused the request. Check the account in the Anthropic console.") from exc
        except anthropic.NotFoundError as exc:
            raise AIUnavailable(f"The AI model {self.model} was not found. Check ANTHROPIC_MODEL.") from exc
        except anthropic.RateLimitError as exc:
            raise AIUnavailable("The AI service is busy or the account is over its limit. Try again in a minute.") from exc
        except anthropic.BadRequestError as exc:
            # Our request was wrong, not the order: worth a retry after a fix, so treat it as unavailable.
            raise AIUnavailable(f"The AI service rejected the request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise AIUnavailable(f"The AI service had a problem ({exc.status_code}). Try again shortly.") from exc
        except anthropic.APIConnectionError as exc:
            raise AIUnavailable("Could not reach the AI service. Check the internet connection.") from exc

        usage = response.usage
        call = ToolCall(
            input=None,
            input_tokens=usage.input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            model=response.model,
        )
        if response.stop_reason == "refusal":
            call.problem = "The AI declined to read this order."
            return call
        if response.stop_reason == "max_tokens":
            call.problem = "The order was too long for one reading."
            return call
        for block in response.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                call.input = block.input if isinstance(block.input, dict) else None
                break
        if call.input is None:
            call.problem = "The AI answered without filling in the order form."
        return call
