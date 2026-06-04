"""Tests for pipeline diagnostic message formatting."""

from agent.pipeline_diagnostics import format_step_error


def test_format_step_error_detects_rate_limit() -> None:
    exc = RuntimeError("Error 429: rate limit exceeded for requests")
    assert "rate limit" in format_step_error("Planner", exc).lower()


def test_format_step_error_detects_quota() -> None:
    exc = RuntimeError("You exceeded your current quota, please check your plan")
    assert "quota" in format_step_error("Curator", exc).lower()


def test_format_step_error_detects_auth_failure() -> None:
    exc = RuntimeError("401 Unauthorized: invalid api key")
    assert "authentication failed" in format_step_error("Planner", exc).lower()


def test_format_step_error_fallback_includes_exception_name() -> None:
    exc = ValueError("something unexpected")
    message = format_step_error("Crawl", exc)
    assert "Crawl failed (ValueError)" in message
    assert "something unexpected" in message
