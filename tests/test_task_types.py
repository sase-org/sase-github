"""Tests for the agent-uncreatable ``github`` task type."""

from pathlib import Path

from sase_github.task_types import (
    GITHUB_TASK_TYPE,
    GITHUB_TASK_TYPE_SPEC,
    GITHUB_TASK_TYPES,
)

ROOT = Path(__file__).resolve().parents[1]


def test_github_task_type_spec_is_agent_uncreatable_with_no_fields() -> None:
    spec = GITHUB_TASK_TYPE_SPEC
    assert spec["task_type"] == GITHUB_TASK_TYPE
    assert spec["label"] == "GitHub"
    assert spec["agent_creatable"] is False
    assert spec["fields"] == []
    assert spec["glyph"] == "⑂"
    assert spec["accent_color"] == "#B2B2B2"
    assert "\n" not in spec["summary"]
    assert len(spec["summary"]) <= 120
    assert len(spec["when_to_use"]) <= 400
    when_to_use = spec["when_to_use"].casefold()
    assert "agents never create this type" in when_to_use
    assert "external issue mirror" in when_to_use


def test_github_task_type_hook_returns_the_spec() -> None:
    assert GITHUB_TASK_TYPES.task_type_specs() == (GITHUB_TASK_TYPE_SPEC,)


def test_github_task_type_spec_validates() -> None:
    from sase.task_types import validate_task_type_spec

    digest = validate_task_type_spec(dict(GITHUB_TASK_TYPE_SPEC))
    assert len(digest) == 64


def test_sase_task_types_entry_point_is_registered() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert '[project.entry-points."sase_task_types"]' in pyproject
    assert "github = " in pyproject
    assert "sase_github.task_types:GITHUB_TASK_TYPES" in pyproject
