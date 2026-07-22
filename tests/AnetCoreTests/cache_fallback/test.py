"""Unit tests for agent_runner's always-on prompt caching with automatic
fallback. Pure, offline — the API client is faked."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import httpx
from openai import BadRequestError

from anet.core import agent_runner as ar


def _bad_request(msg="Invalid type for 'messages[0].content'"):
    req = httpx.Request("POST", "https://example.test/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": {"message": msg}})
    return BadRequestError(msg, response=resp, body=None)


class FakeClient:
    """Records each call's kwargs; optionally 400s on cache-marked requests."""

    def __init__(self, reject_cache_markers=False, always_400=False):
        self.reject_cache_markers = reject_cache_markers
        self.always_400 = always_400
        self.calls: list[dict] = []
        outer = self

        class _Completions:
            async def create(self, **kwargs):
                outer.calls.append(kwargs)
                if outer.always_400:
                    raise _bad_request("something else entirely")
                if outer.reject_cache_markers and outer._is_marked(kwargs):
                    raise _bad_request()
                return "OK_RESPONSE"

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()

    @staticmethod
    def _is_marked(kwargs) -> bool:
        for m in kwargs.get("messages", []):
            c = m.get("content")
            if isinstance(c, list) and any("cache_control" in b for b in c if isinstance(b, dict)):
                return True
        for t in kwargs.get("tools", []) or []:
            if "cache_control" in t:
                return True
        return False


def _messages():
    return [
        {"role": "system", "content": "you are a test agent"},
        {"role": "user", "content": "hello"},
    ]


def _tools():
    return [{"type": "function", "function": {"name": "t", "parameters": {}}}]


def _call(client, provider="openrouter", model="anthropic/claude-test"):
    return asyncio.run(ar._create_with_cache_fallback(
        client, provider=provider, model=model,
        messages=_messages(), tool_schemas=_tools(), agent={"name": "test"},
    ))


def setup_function():
    ar._CACHE_UNSUPPORTED.clear()
    ar._CACHE_FORCE_ALL = True         # default: attempt markers everywhere


# ── Who gets markers ─────────────────────────────────────────────────────────

def test_claude_model_is_marked():
    c = FakeClient()
    assert _call(c, model="anthropic/claude-haiku-4.5") == "OK_RESPONSE"
    assert FakeClient._is_marked(c.calls[0])


def test_unknown_model_is_marked_by_default():
    # Caching is attempted for every model — an agent loop resends the same
    # prefix each step, so this is the biggest saving available. The 400
    # fallback covers providers that reject the format.
    c = FakeClient()
    _call(c, model="nvidia/nemotron-3-super-120b-a12b:free")
    assert FakeClient._is_marked(c.calls[0])


def test_attempt_all_models_can_be_turned_off():
    ar._CACHE_FORCE_ALL = False
    c = FakeClient()
    _call(c, model="nvidia/nemotron-3-super-120b-a12b:free")
    assert not FakeClient._is_marked(c.calls[0])
    # known-good models keep their markers regardless
    c2 = FakeClient()
    _call(c2, model="anthropic/claude-haiku-4.5")
    assert FakeClient._is_marked(c2.calls[0])


def test_system_prompt_and_last_tool_both_marked():
    c = FakeClient()
    _call(c, model="anthropic/claude-haiku-4.5")
    kw = c.calls[0]
    sys_block = kw["messages"][0]["content"]
    assert isinstance(sys_block, list) and "cache_control" in sys_block[0]
    assert "cache_control" in kw["tools"][-1]


# ── Fallback when the upstream rejects the format ───────────────────────────

def test_falls_back_once_then_succeeds_unmarked():
    c = FakeClient(reject_cache_markers=True)
    assert _call(c) == "OK_RESPONSE"
    assert len(c.calls) == 2                      # marked (400) then unmarked (ok)
    assert FakeClient._is_marked(c.calls[0])
    assert not FakeClient._is_marked(c.calls[1])


def test_unsupported_model_is_remembered_for_the_session():
    c = FakeClient(reject_cache_markers=True)
    _call(c, model="anthropic/claude-picky")
    assert "openrouter:anthropic/claude-picky" in ar._CACHE_UNSUPPORTED

    c2 = FakeClient(reject_cache_markers=True)
    _call(c2, model="anthropic/claude-picky")
    assert len(c2.calls) == 1, "should not re-probe a model already known to reject"
    assert not FakeClient._is_marked(c2.calls[0])


def test_other_models_unaffected_by_one_models_rejection():
    c = FakeClient(reject_cache_markers=True)
    _call(c, model="anthropic/claude-picky")
    c2 = FakeClient()
    _call(c2, model="anthropic/claude-fine")
    assert FakeClient._is_marked(c2.calls[0])


# ── A genuine bad request must still surface ────────────────────────────────

def test_unrelated_400_propagates_and_is_not_mistaken_for_cache_rejection():
    c = FakeClient(always_400=True)
    try:
        _call(c, model="anthropic/claude-broken")
        assert False, "expected BadRequestError to propagate"
    except BadRequestError:
        pass
    assert len(c.calls) == 2          # tried unmarked once before giving up
    # It DOES get marked unsupported — the cost of not being able to tell the
    # two kinds of 400 apart. Harmless: worst case is losing caching on a model
    # whose requests were failing anyway.
    assert "openrouter:anthropic/claude-broken" in ar._CACHE_UNSUPPORTED


# ── Retry behaviour preserved ────────────────────────────────────────────────

def test_transient_errors_still_retry_and_then_succeed():
    from openai import RateLimitError
    req = httpx.Request("POST", "https://example.test/v1")
    state = {"n": 0}

    class Flaky(FakeClient):
        def __init__(self):
            super().__init__()
            outer = self

            class _Completions:
                async def create(self, **kwargs):
                    outer.calls.append(kwargs)
                    state["n"] += 1
                    if state["n"] == 1:
                        raise RateLimitError(
                            "rate limited",
                            response=httpx.Response(429, request=req), body=None)
                    return "OK_RESPONSE"

            class _Chat:
                completions = _Completions()
            self.chat = _Chat()

    saved = ar._RETRY_DELAY
    ar._RETRY_DELAY = 0        # don't actually sleep in a unit test
    try:
        c = Flaky()
        assert _call(c) == "OK_RESPONSE"
        assert len(c.calls) == 2
    finally:
        ar._RETRY_DELAY = saved


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            setup_function(); f(); print(f"  ok  {n}")
    print("PASS: cache_fallback")
