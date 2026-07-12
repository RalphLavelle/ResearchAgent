"""Tests for LLM backend selection and startup probing (no real API calls)."""

from __future__ import annotations

import pytest

from agent.llm_factory import verify_llm_at_startup


# ── Helpers ──────────────────────────────────────────────────────────────────

class _OkResp:
    """Fake httpx response that doesn't raise."""
    def raise_for_status(self) -> None:
        pass


class _OkClient:
    """Fake httpx.Client context manager returning _OkResp."""
    def __enter__(self) -> "_OkClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, _url: str, headers=None) -> _OkResp:
        return _OkResp()


class _BrokenClient:
    """Fake httpx.Client that raises on every request."""
    def __enter__(self) -> "_BrokenClient":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, _url: str, headers=None) -> None:
        raise OSError("connection refused")


def _patch_ok_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent.llm_factory.httpx.Client",
        lambda *_a, **_k: _OkClient(),
    )


def _patch_broken_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent.llm_factory.httpx.Client",
        lambda *_a, **_k: _BrokenClient(),
    )


def _patch_local_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Standard local-Ollama config (non-cloud)."""
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr("agent.config.OLLAMA_API_KEY", "ollama")
    monkeypatch.setattr("agent.config.OLLAMA_MODEL", "qwen3:8b")


# ── No backend enabled ──────────────────────────────────────────────────────

def test_verify_fails_when_no_backend_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)
    assert verify_llm_at_startup() is False


# ── Cloud OpenAI ─────────────────────────────────────────────────────────────

def test_verify_fails_when_openai_enabled_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)
    monkeypatch.setattr("agent.config.OPENAI_API_KEY", "")
    assert verify_llm_at_startup() is False


def test_verify_openai_passes_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)
    monkeypatch.setattr("agent.config.OPENAI_API_KEY", "sk-test")
    assert verify_llm_at_startup() is True


# ── Local Ollama ─────────────────────────────────────────────────────────────

def test_verify_ollama_passes_when_models_endpoint_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_local_ollama(monkeypatch)
    _patch_ok_http(monkeypatch)
    assert verify_llm_at_startup() is True


def test_verify_ollama_fails_when_request_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_local_ollama(monkeypatch)
    _patch_broken_http(monkeypatch)
    assert verify_llm_at_startup() is False


# ── Ollama Cloud (model has :cloud suffix) ───────────────────────────────────

def test_verify_cloud_model_passes_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A :cloud model with a real API key should pass verification."""
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr("agent.config.OLLAMA_API_KEY", "abc123.real_key")
    monkeypatch.setattr("agent.config.OLLAMA_MODEL", "kimi-k2.6:cloud")
    _patch_ok_http(monkeypatch)
    assert verify_llm_at_startup() is True


def test_verify_cloud_model_compound_tag_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model tag like '120b-cloud' (cloud embedded in tag) should be detected as cloud."""
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr("agent.config.OLLAMA_API_KEY", "abc123.real_key")
    monkeypatch.setattr("agent.config.OLLAMA_MODEL", "gpt-oss:120b-cloud")
    _patch_ok_http(monkeypatch)
    assert verify_llm_at_startup() is True


def test_verify_cloud_model_fails_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A :cloud model with the default placeholder key should fail."""
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr("agent.config.OLLAMA_API_KEY", "ollama")
    monkeypatch.setattr("agent.config.OLLAMA_MODEL", "kimi-k2.6:cloud")
    assert verify_llm_at_startup() is False


# ── Ollama Cloud (remote base URL) ──────────────────────────────────────────

def test_verify_remote_url_passes_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct cloud API (remote base_url) with a real key should pass."""
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_BASE_URL", "https://ollama.com/v1")
    monkeypatch.setattr("agent.config.OLLAMA_API_KEY", "abc123.real_key")
    monkeypatch.setattr("agent.config.OLLAMA_MODEL", "kimi-k2.6")
    _patch_ok_http(monkeypatch)
    assert verify_llm_at_startup() is True


def test_verify_remote_url_still_passes_when_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloud verification is lenient — a failing /models probe does not block startup."""
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_BASE_URL", "https://ollama.com/v1")
    monkeypatch.setattr("agent.config.OLLAMA_API_KEY", "abc123.real_key")
    monkeypatch.setattr("agent.config.OLLAMA_MODEL", "kimi-k2.6")
    _patch_broken_http(monkeypatch)
    assert verify_llm_at_startup() is True


def test_verify_remote_url_fails_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct cloud API without an API key should fail."""
    monkeypatch.setattr("agent.config.OPENAI_ENABLED", False)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_BASE_URL", "https://ollama.com/v1")
    monkeypatch.setattr("agent.config.OLLAMA_API_KEY", "")
    monkeypatch.setattr("agent.config.OLLAMA_MODEL", "kimi-k2.6")
    assert verify_llm_at_startup() is False


# ── Planner temperature ─────────────────────────────────────────────────────

def test_sample_planner_temperature_stays_in_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from random import Random

    from agent.llm_factory import sample_planner_temperature

    monkeypatch.setattr("agent.config.PLANNER_TEMPERATURE_MIN", 0.2)
    monkeypatch.setattr("agent.config.PLANNER_TEMPERATURE_MAX", 0.8)
    rng = Random(42)
    samples = [sample_planner_temperature(rng=rng) for _ in range(30)]
    assert all(0.2 <= s <= 0.8 for s in samples)
    # With a fixed seed the sequence is deterministic and not all identical.
    assert len(set(round(s, 5) for s in samples)) > 1


def test_sample_planner_temperature_fixed_when_min_equals_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.llm_factory import sample_planner_temperature

    monkeypatch.setattr("agent.config.PLANNER_TEMPERATURE_MIN", 0.55)
    monkeypatch.setattr("agent.config.PLANNER_TEMPERATURE_MAX", 0.55)
    assert sample_planner_temperature() == 0.55


def test_build_planner_llm_uses_explicit_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.llm_factory import build_planner_llm

    monkeypatch.setattr("agent.config.OPENAI_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)
    monkeypatch.setattr("agent.config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("agent.config.OPENAI_MODEL", "gpt-4o")

    llm = build_planner_llm(temperature=0.37)
    assert float(llm.temperature) == 0.37


def test_build_chat_llm_stays_at_zero_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Curator / tagging / dedupe must stay deterministic (temperature 0)."""
    from agent.llm_factory import build_chat_llm

    monkeypatch.setattr("agent.config.OPENAI_ENABLED", True)
    monkeypatch.setattr("agent.config.OLLAMA_ENABLED", False)
    monkeypatch.setattr("agent.config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("agent.config.OPENAI_MODEL", "gpt-4o")

    llm = build_chat_llm()
    assert float(llm.temperature) == 0
