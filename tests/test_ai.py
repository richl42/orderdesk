"""pipeline/ai.py: the request it sends, what it reports back, cost, and the answer cache."""

from decimal import Decimal
from types import SimpleNamespace

import httpx2
import pytest

from pipeline.ai import AIUnavailable, AnthropicAI, ToolCall, cost_gbp
from pipeline.classify_extract import TOOL


class StubMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests = response, error, []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def response(content, stop_reason="tool_use"):
    usage = SimpleNamespace(input_tokens=300, cache_creation_input_tokens=1800, cache_read_input_tokens=0,
                            output_tokens=450)
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage, model="claude-sonnet-5-5")


def ai_with(stub, cache_dir=None):
    ai = AnthropicAI("key", "claude-sonnet-5-5", cache_dir=cache_dir)
    ai._client = SimpleNamespace(messages=stub)
    return ai


TOOL_BLOCK = SimpleNamespace(type="tool_use", name="record_order", input={"classification": "other"})


def test_request_is_a_strict_tool_with_the_system_prompt_cached():
    stub = StubMessages(response([SimpleNamespace(type="thinking", thinking=""), TOOL_BLOCK]))
    call = ai_with(stub).call_tool(system="rules", user="order text", tool=TOOL)
    request = stub.requests[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["tools"][0]["strict"] is True and request["tools"][0]["name"] == "record_order"
    assert request["tool_choice"] == {"type": "auto"}
    assert "temperature" not in request  # Claude Sonnet 5.5 refuses a fixed temperature
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["messages"] == [{"role": "user", "content": "order text"}]
    assert call.input == {"classification": "other"} and call.problem is None
    assert (call.input_tokens, call.cache_write_tokens, call.output_tokens) == (300, 1800, 450)
    assert call.total_input_tokens == 2100


@pytest.mark.parametrize("content, stop, problem", [
    ([SimpleNamespace(type="text", text="Here is the order...")], "end_turn", "without filling in"),
    ([], "refusal", "declined"),
    ([TOOL_BLOCK], "max_tokens", "too long"),
])
def test_no_usable_tool_call_is_reported(content, stop, problem):
    call = ai_with(StubMessages(response(content, stop))).call_tool(system="s", user="u", tool=TOOL)
    assert call.input is None and problem in call.problem


def test_service_problems_become_plain_messages():
    import anthropic

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    with pytest.raises(AIUnavailable, match="Could not reach the AI service"):
        ai_with(StubMessages(error=error)).call_tool(system="s", user="u", tool=TOOL)
    with pytest.raises(AIUnavailable, match="No AI key"):
        AnthropicAI("", "claude-sonnet-5-5").call_tool(system="s", user="u", tool=TOOL)


def test_answers_are_cached_by_the_whole_request_and_attempt(tmp_path):
    stub = StubMessages(response([TOOL_BLOCK]))
    ai = ai_with(stub, cache_dir=tmp_path)
    first = ai.call_tool(system="s", user="u", tool=TOOL)
    again = ai.call_tool(system="s", user="u", tool=TOOL)
    assert len(stub.requests) == 1
    assert again.cached and not first.cached
    assert again.input == first.input and again.output_tokens == first.output_tokens
    ai.call_tool(system="s", user="u", tool=TOOL, attempt=2)  # a retry is a new request
    ai.call_tool(system="s", user="different order", tool=TOOL)
    assert len(stub.requests) == 3


def test_cost_in_pounds_counts_cache_writes_and_reads():
    call = ToolCall(input={}, input_tokens=1_000_000, cache_write_tokens=1_000_000, cache_read_tokens=1_000_000,
                    output_tokens=100_000)
    cost = cost_gbp(call, Decimal("1.60"), Decimal("8.00"))
    assert cost == Decimal("1.60") + Decimal("2.00") + Decimal("0.16") + Decimal("0.80")
    assert cost_gbp(call, None, Decimal("8.00")) is None
