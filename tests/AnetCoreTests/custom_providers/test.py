"""Unit tests for custom OpenAI-compatible providers declared under
`providers:` in anet.config.yaml. Pure, offline — config_loader is faked."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anet.core import agent_runner as ar


def _with_config(cfg, fn):
    import anet.core.config_loader as cl
    saved = cl.load
    cl.load = lambda: cfg
    try:
        return fn()
    finally:
        cl.load = saved


_OLLAMA = {"providers": {"ollama": {"base_url": "http://localhost:11434/v1"}}}


# ── Registry merging ─────────────────────────────────────────────────────────

def test_builtins_present_without_any_config():
    got = _with_config({}, lambda: set(ar._PROVIDERS))
    assert {"openrouter", "google", "openai"} <= got


def test_custom_provider_is_visible_to_membership_and_lookup():
    def check():
        assert "ollama" in ar._PROVIDERS          # `provider in _PROVIDERS`
        assert ar._PROVIDERS["ollama"]["base_url"] == "http://localhost:11434/v1"
        assert ar._PROVIDERS.get("ollama")        # `.get(...)`
        return True
    assert _with_config(_OLLAMA, check)


def test_custom_provider_does_not_hide_builtins():
    got = _with_config(_OLLAMA, lambda: set(ar._PROVIDERS))
    assert "ollama" in got and "openrouter" in got


def test_config_can_override_a_builtin_base_url():
    cfg = {"providers": {"openai": {"base_url": "https://proxy.internal/v1",
                                    "env_key": "OPENAI_API_KEY"}}}
    url = _with_config(cfg, lambda: ar._PROVIDERS["openai"]["base_url"])
    assert url == "https://proxy.internal/v1"


def test_registry_reflects_config_changes_without_restart():
    before = _with_config({}, lambda: "ollama" in ar._PROVIDERS)
    after  = _with_config(_OLLAMA, lambda: "ollama" in ar._PROVIDERS)
    assert before is False and after is True


# ── Malformed entries are ignored, not allowed to build a broken client ─────

def test_entry_without_base_url_is_ignored():
    cfg = {"providers": {"broken": {"env_key": "X"}}}
    assert _with_config(cfg, lambda: "broken" in ar._PROVIDERS) is False


def test_non_dict_entries_are_ignored():
    cfg = {"providers": {"bad": "not-a-dict", "ok": {"base_url": "http://x/v1"}}}
    got = _with_config(cfg, lambda: set(ar._PROVIDERS))
    assert "bad" not in got and "ok" in got


def test_providers_block_of_wrong_type_is_ignored():
    assert _with_config({"providers": ["nope"]}, lambda: set(ar._PROVIDERS)) >= {"openrouter"}


def test_provider_names_are_lowercased():
    cfg = {"providers": {"OllaMa": {"base_url": "http://x/v1"}}}
    assert _with_config(cfg, lambda: "ollama" in ar._PROVIDERS)


# ── Client construction ──────────────────────────────────────────────────────

def test_client_for_keyless_local_provider():
    def check():
        c = ar._build_openai_client("ollama")
        assert str(c.base_url).rstrip("/") == "http://localhost:11434/v1"
        return True
    assert _with_config(_OLLAMA, check)


def test_client_uses_declared_env_key():
    cfg = {"providers": {"gw": {"base_url": "http://gw/v1", "env_key": "GW_TEST_KEY"}}}
    os.environ["GW_TEST_KEY"] = "secret-value"
    try:
        c = _with_config(cfg, lambda: ar._build_openai_client("gw"))
        assert c.api_key == "secret-value"
    finally:
        os.environ.pop("GW_TEST_KEY", None)


# ── The manager path uses the same registry (no second copy) ────────────────

def test_manager_client_resolves_a_custom_provider():
    from anet.core import engine_base
    cfg = {
        "providers": {"ollama": {"base_url": "http://localhost:11434/v1"}},
        "manager": {"model": "llama3", "provider": "ollama"},
    }
    import anet.core.config_loader as cl
    saved_load, saved_mgr = cl.load, cl.manager_config
    cl.load = lambda: cfg
    cl.manager_config = lambda: cfg["manager"]
    try:
        client, model = engine_base._manager_client()
    finally:
        cl.load, cl.manager_config = saved_load, saved_mgr
    assert model == "llama3"
    assert str(client.base_url).rstrip("/") == "http://localhost:11434/v1"


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print(f"  ok  {n}")
    print("PASS: custom_providers")
