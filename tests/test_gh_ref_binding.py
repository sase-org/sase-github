"""`#gh` input binding for owner/repo refs and other supported forms."""

from pathlib import Path

import pytest
from sase.macro.input_binding import bind_input_args
from sase.macro.workflow_loader_definition import load_workflow_from_file

_GH_YML = (
    Path(__file__).resolve().parents[1] / "src" / "sase_github" / "xprompts" / "gh.yml"
)


@pytest.mark.parametrize(
    "gh_ref",
    [
        "bobs-org/bob-cli",
        "ghe.example.test/acme/widget",
        "gh_sase-org__sase",
        "my-feature",
    ],
)
def test_owner_repo_refs_bind(gh_ref: str) -> None:
    workflow = load_workflow_from_file(_GH_YML)
    assert workflow is not None
    bound = bind_input_args(workflow.inputs, [gh_ref], {})
    assert bound.values["gh_ref"] == gh_ref
