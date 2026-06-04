"""Human-readable pipeline step failure reasons for run reports."""

from __future__ import annotations


def format_step_error(step: str, exc: Exception) -> str:
    """Turn an exception into a short, actionable report line."""
    name = type(exc).__name__
    msg = str(exc).strip() or name
    low = msg.lower()

    if any(token in low for token in ("rate limit", "rate_limit", "too many requests")) or "429" in msg:
        return f"{step}: API rate limit or quota exceeded — {msg}"
    if any(token in low for token in ("insufficient_quota", "billing", "exceeded your current quota")):
        return f"{step}: API quota or billing limit reached — {msg}"
    if any(
        token in low
        for token in ("401", "unauthorized", "invalid api key", "authentication", "permission denied")
    ):
        return f"{step}: authentication failed — check API key and credentials in .env — {msg}"
    if any(token in low for token in ("timeout", "timed out", "connection refused", "connect error")):
        return f"{step}: LLM or network timeout — service may be down or overloaded — {msg}"
    if "503" in msg or "502" in msg or "service unavailable" in low:
        return f"{step}: upstream service unavailable — {msg}"
    if "could not extract valid json" in low:
        return (
            f"{step}: LLM response was not valid JSON — model may have returned prose "
            f"instead of structured output — {msg}"
        )

    return f"{step} failed ({name}): {msg}"
