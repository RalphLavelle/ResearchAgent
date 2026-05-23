"""Tests for per-topic prompt_guides.yaml loading."""

from pathlib import Path

from agent.prompt_guides import PromptGuides, load_prompt_guides


def test_load_prompt_guides_defaults_when_missing(tmp_path: Path) -> None:
    guides = load_prompt_guides(tmp_path / "missing.yaml")
    assert guides.resource_label_plural == "events"
    assert guides.planner_date_suffix == ""


def test_load_prompt_guides_from_repo_topic() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "topics" / "live-music-brisbane-gold-coast" / "prompt_guides.yaml"
    guides = load_prompt_guides(path)
    assert guides.resource_label_plural == "gigs and concerts"
    assert "Gold Coast first" in guides.planner_date_suffix
    assert "Gold Coast balance" in guides.curator_date_suffix


def test_event_window_uses_topic_suffix() -> None:
    from agent.event_window import curator_date_instruction, planner_date_instruction

    guides = PromptGuides(
        resource_label_plural="workshops",
        planner_date_suffix="Prefer downtown venues.",
        curator_date_suffix="Skip online-only events.",
    )
    planner = planner_date_instruction(guides)
    assert "workshops" in planner
    assert "Prefer downtown venues." in planner
    assert "Gold Coast" not in planner

    curator = curator_date_instruction(guides)
    assert "Skip online-only events." in curator
