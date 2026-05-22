"""Pydantic models for curated resources and workflow state.

The Resource model is intentionally generic — field names and descriptions
are kept neutral so that the same model works for any research topic. The
subject_matter.yaml file drives what the LLM puts *into* each field; the
model just validates that the shape is correct.
"""

from __future__ import annotations

from typing import TypedDict
from uuid import uuid4

from pydantic import BaseModel, Field


class Resource(BaseModel):
    """One curated result written to the local Markdown output file."""

    # Stable id for spreadsheet + JSON (not shown in the Angular table UI).
    id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Stable UUID for deduplication and APIs.",
    )
    title: str = Field(..., description="Display title of the event or resource.")
    url: str = Field(..., description="Canonical http(s) URL for the item.")

    # Date is especially relevant for event-based topics; left blank otherwise.
    date: str = Field(
        default="",
        description="Date of the event or publication, if available.",
    )
    summary: str = Field(
        default="",
        description="One or two sentences explaining why this item is worth listing.",
    )
    thumbnail_url: str | None = Field(
        default=None,
        description="Open Graph or preview image URL when available.",
    )


class ResourceListPayload(BaseModel):
    """Structured LLM output for the normalisation step."""

    resources: list[Resource] = Field(default_factory=list)


class PlanQueries(BaseModel):
    """Structured LLM output for the query-planning step."""

    queries: list[str] = Field(
        default_factory=list,
        description="Distinct web-search queries covering the active research topic.",
    )


class AgentState(TypedDict, total=False):
    """LangGraph workflow state passed between nodes."""

    queries: list[str]
    raw_search_text: str
    crawled_urls: list[str]
    resources: list[dict]
    fingerprint: str
    fingerprint_unchanged: bool
    run_log_message: str
    dry_run: bool
    skip_doc_rewrite: bool


def resource_from_dict(data: dict) -> Resource:
    """Build a Resource from a graph-state dictionary.

    Older pipeline dumps may still contain dropped keys such as ``resource_type``,
    ``price``, or ``participatory``; those are ignored.
    """
    rid = data.get("id")
    kwargs: dict = dict(
        title=str(data.get("title", "")),
        url=str(data.get("url", "")),
        date=str(data.get("date", "")),
        summary=str(data.get("summary", "")),
        thumbnail_url=data.get("thumbnail_url"),
    )
    if isinstance(rid, str) and rid.strip():
        kwargs["id"] = rid.strip()
    return Resource(**kwargs)


def resource_to_dict(r: Resource) -> dict:
    """Serialise a Resource to a plain dict for JSON snapshot storage."""
    return {
        "id": r.id,
        "title": r.title,
        "url": r.url,
        "date": r.date,
        "summary": r.summary,
        "thumbnail_url": r.thumbnail_url,
    }
