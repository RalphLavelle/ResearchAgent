"""Unit tests for cloud-model structured-output parsing."""

from __future__ import annotations

from agent.models import PlanQueries, ResourceListPayload
from agent.structured_output import (
    _coerce_schema_echo,
    _extract_json,
    _parse_numbered_queries,
    _recover_from_plain_text,
    _repair_truncated_json,
)


def test_coerce_schema_echo_hoists_properties_queries() -> None:
    parsed = {
        "description": "Structured LLM output for the query-planning step.",
        "properties": {
            "queries": [
                "site:facebook.com/events Gold Coast June 2026",
                "open mic nights Burleigh Heads 2026",
            ]
        },
        "title": "PlanQueries",
        "type": "object",
    }
    coerced = _coerce_schema_echo(parsed, PlanQueries)
    plan = PlanQueries.model_validate(coerced)
    assert len(plan.queries) == 2
    assert "Burleigh" in plan.queries[1]


def test_extract_json_prefers_data_object_over_schema_echo() -> None:
    text = """
Here you go:
```json
{
  "description": "Structured LLM output for the query-planning step.",
  "properties": {
    "queries": ["schema echo query"]
  },
  "type": "object"
}
```

```json
{
  "queries": [
    "real query one",
    "real query two"
  ]
}
```
"""
    parsed = _extract_json(text, output_model=PlanQueries)
    plan = PlanQueries.model_validate(_coerce_schema_echo(parsed, PlanQueries))
    assert plan.queries[0] == "real query one"


def test_parse_numbered_queries() -> None:
    text = """
1. "live music" Gold Coast June 2026
2. site:eventbrite.com.au "open mic" Gold Coast
3) who is playing in Burleigh this weekend?
"""
    queries = _parse_numbered_queries(text)
    assert len(queries) == 3
    assert queries[0].startswith('"live music"')
    assert "eventbrite" in queries[1]


def test_recover_from_plain_text_for_plan_queries() -> None:
    text = "1. jazz gigs Gold Coast July 2026\n2. blues Brisbane 2026"
    plan = _recover_from_plain_text(text, PlanQueries)
    assert plan is not None
    assert len(plan.queries) == 2


def test_repair_truncated_json_salvages_complete_resources() -> None:
    # Mirrors the real failure: a ```json reply cut off mid-URL, so the fence,
    # the final object, and the closing brackets are all missing.
    text = (
        '```json\n'
        '{\n'
        '  "resources": [\n'
        '    {\n'
        '      "title": "Te Wehi @ Miami Marketta, Gold Coast",\n'
        '      "url": "https://www.miamimarketta.com/ticketed-events",\n'
        '      "date": "2026-07-11",\n'
        '      "summary": "Album tour.",\n'
        '      "thumbnail_url": null\n'
        '    },\n'
        '    {\n'
        '      "title": "The Beths @ The Tivoli, Brisbane",\n'
        '      "url": "https://example.com/gig",\n'
        '      "date": "2026-07-15",\n'
        '      "summary": "Indie rock.",\n'
        '      "thumbnail_url": "https://images.squarespace-cdn.com/content/v1/619ae50a'
    )
    repaired = _repair_truncated_json(text)
    assert isinstance(repaired, dict)
    payload = ResourceListPayload.model_validate(repaired)
    # The first, complete event is kept; the truncated one is dropped.
    assert len(payload.resources) == 1
    assert payload.resources[0].title.startswith("Te Wehi")


def test_extract_json_falls_back_to_repair_when_truncated() -> None:
    text = '```json\n{"resources": [{"title": "A", "url": "https://x/a", "date": "2026-08-01"'
    parsed = _extract_json(text, output_model=ResourceListPayload)
    payload = ResourceListPayload.model_validate(parsed)
    assert len(payload.resources) == 1
    assert payload.resources[0].title == "A"


def test_repair_truncated_json_returns_none_for_non_json() -> None:
    assert _repair_truncated_json("no json here at all") is None
