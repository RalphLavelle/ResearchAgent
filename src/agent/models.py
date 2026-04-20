"""Pydantic models for curated resources and workflow state."""

from __future__ import annotations

from enum import Enum
from typing import TypedDict

from pydantic import BaseModel, Field


class ResourceType(str, Enum):
    """Kind of learning material."""

    book = "book"
    ebook = "ebook"
    course = "course"
    website = "website"


class Resource(BaseModel):
    """One curated learning resource (written to local Markdown output)."""

    title: str = Field(..., description="Display title")
    url: str = Field(..., description="Canonical URL for the resource")
    resource_type: ResourceType
    price: str = Field(
        default="Unknown",
        description="Human-readable price: Free, $49, £12.99, Unknown, etc.",
    )
    summary: str = Field(
        default="",
        description="Why this is high-quality or relevant for learning about AI agents.",
    )
    langgraph_specific: bool = Field(
        default=False,
        description="True if this is specifically about LangGraph (when framework-specific).",
    )
    thumbnail_url: str | None = Field(
        default=None,
        description="Open Graph or preview image URL when available.",
    )


class ResourceListPayload(BaseModel):
    """Structured LLM output for normalization."""

    resources: list[Resource] = Field(default_factory=list)


class PlanQueries(BaseModel):
    """LLM output for search query planning."""

    queries: list[str] = Field(
        default_factory=list,
        description="Distinct DuckDuckGo queries covering books, courses, sites, LangGraph.",
    )


class AgentState(TypedDict, total=False):
    """LangGraph state."""

    queries: list[str]
    raw_search_text: str
    resources: list[dict]
    fingerprint: str
    fingerprint_unchanged: bool
    run_log_message: str
    dry_run: bool
    skip_doc_rewrite: bool


def resource_from_dict(data: dict) -> Resource:
    """Build Resource from graph state dict."""
    rt = data.get("resource_type")
    if isinstance(rt, ResourceType):
        rtype = rt
    else:
        try:
            rtype = ResourceType(str(rt or "website"))
        except ValueError:
            rtype = ResourceType.website
    return Resource(
        title=str(data.get("title", "")),
        url=str(data.get("url", "")),
        resource_type=rtype,
        price=str(data.get("price", "Unknown")),
        summary=str(data.get("summary", "")),
        langgraph_specific=bool(data.get("langgraph_specific", False)),
        thumbnail_url=data.get("thumbnail_url"),
    )


def resource_to_dict(r: Resource) -> dict:
    """Serialize Resource for JSON snapshot."""
    return {
        "title": r.title,
        "url": r.url,
        "resource_type": r.resource_type.value,
        "price": r.price,
        "summary": r.summary,
        "langgraph_specific": r.langgraph_specific,
        "thumbnail_url": r.thumbnail_url,
    }
