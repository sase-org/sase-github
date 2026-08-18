"""Declarative ``github`` task-type spec for the sase-github plugin.

Mirrored GitHub issues are already ``task`` beads created by sase's external
issue mirror. This spec stamps that origin as an agent-uncreatable catalog
member; it does not add an ``IssueType.GITHUB``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sase.task_types import hookimpl

_TASK_TYPE_SPEC_SCHEMA_VERSION = 1

GITHUB_TASK_TYPE = "github"

GITHUB_TASK_TYPE_SPEC: Mapping[str, Any] = {
    "schema_version": _TASK_TYPE_SPEC_SCHEMA_VERSION,
    "task_type": GITHUB_TASK_TYPE,
    "label": "GitHub",
    "summary": (
        "A GitHub issue mirrored into a task bead; agents never create this type."
    ),
    "when_to_use": (
        "Agents never create this type. Beads of it are created by the "
        "external issue mirror when it covers a GitHub issue. Do not file "
        "one from `sase bead create` or `/sase_new_task`."
    ),
    "glyph": "⑂",
    "accent_color": "#B2B2B2",
    "agent_creatable": False,
    "fields": [],
}


class _GitHubTaskTypes:
    """Pluggy hookimpl exposing the ``github`` task-type spec."""

    @hookimpl
    def task_type_specs(self) -> tuple[Mapping[str, Any], ...]:
        return (GITHUB_TASK_TYPE_SPEC,)


GITHUB_TASK_TYPES = _GitHubTaskTypes()
