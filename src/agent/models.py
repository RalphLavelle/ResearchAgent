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

    # The valid values for resource_type are declared in subject_matter.yaml
    # (resource_types key) and enforced by the curator LLM prompt. Using a
    # plain string here keeps the model topic-agnostic.
    resource_type: str = Field(
        default="website",
        description="Category label as defined in the active subject_matter.yaml.",
    )
    price: str = Field(
        default="Unknown",
        description="Human-readable price: Free, $25, Unknown, etc.",
    )
    # Date is especially relevant for event-based topics; left blank otherwise.
    date: str = Field(
        default="",
        description="Date of the event or publication, if available.",
    )
    summary: str = Field(
        default="",
        description="One or two sentences explaining why this item is worth listing.",
    )
    # For music events: True when the audience can actively perform (open mic,
    # jam session, etc.). For other topics this can be left False.
    participatory: bool = Field(
        default=False,
        description="True if attendees can actively participate (e.g. open mic night).",
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
    resources: list[dict]
    fingerprint: str
    fingerprint_unchanged: bool
    run_log_message: str
    dry_run: bool
    skip_doc_rewrite: bool


def resource_from_dict(data: dict) -> Resource:
    """Build a Resource from a graph-state dictionary.

    Handles both the new field names (participatory, date) and the old ones
    (langgraph_specific) so that existing snapshot files still load without
    crashing — old snapshots will just have participatory defaulting to False.
    """
    # Support old snapshots that still have 'langgraph_specific'
    participatory = data.get("participatory") or data.get("langgraph_specific", False)
    rid = data.get("id")
    kwargs: dict = dict(
        title=str(data.get("title", "")),
        url=str(data.get("url", "")),
        resource_type=str(data.get("resource_type", "website")),
        price=str(data.get("price", "Unknown")),
        date=str(data.get("date", "")),
        summary=str(data.get("summary", "")),
        participatory=bool(participatory),
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
        "participatory": r.participatory,
        "thumbnail_url": r.thumbnail_url,
    }
