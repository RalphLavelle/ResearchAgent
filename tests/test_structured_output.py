"""Unit tests for cloud-model structured-output parsing."""

from __future__ import annotations

from agent.models import PlanQueries
from agent.structured_output import (
    _coerce_schema_echo,
    _extract_json,
    _parse_numbered_queries,
    _recover_from_plain_text,
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
