from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def test_ci_builds_coordinated_sase_sources() -> None:
    workflow = _read(".github/workflows/ci.yml")

    assert "SASE_PYTHON_PATH: .ci/sase" in workflow
    assert "SASE_RUST_CORE_PATH: .ci/sase-core" in workflow
    assert workflow.count("repository: sase-org/sase\n") == 2
    assert workflow.count("repository: sase-org/sase-core\n") == 2
    assert workflow.count("uses: dtolnay/rust-toolchain@stable") == 2
    assert 'python-version: ["3.12", "3.13"]' in workflow
    assert "uv venv --python 3.12 .venv" in workflow
    assert "uv venv --python ${{ matrix.python-version }} .venv" in workflow
    assert workflow.count("run: just install") == 2


def test_task_runner_requires_both_source_overrides() -> None:
    justfile = _read("Justfile")

    assert "SASE_PYTHON_PATH and SASE_RUST_CORE_PATH must be set together" in justfile
    assert "crates/sase_core_py" in justfile
    assert justfile.count("set -eu") == 2
    assert '"$venv_path/bin/maturin" develop --release' in justfile
    assert "PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1" in justfile
    assert (
        'uv pip install --python "$python_path" --no-deps -e "$sase_python_path"'
        in justfile
    )
    assert 'just install-source-sase "$venv_path/bin/python"' in justfile
    assert 'uv pip install --python {{ venv_bin }}/python -e ".[dev]"' in justfile
    assert "SASE_CORE_PATH" not in justfile


def test_release_smoke_builds_coordinated_sase_sources() -> None:
    workflow = _read(".github/workflows/publish.yml")
    smoke_job = workflow.split("  install-smoke:\n", maxsplit=1)[1].split(
        "  publish:\n", maxsplit=1
    )[0]

    assert "repository: sase-org/sase\n" in smoke_job
    assert "repository: sase-org/sase-core\n" in smoke_job
    assert "uses: dtolnay/rust-toolchain@stable" in smoke_job
    assert "just install-source-sase /tmp/smoke-venv/bin/python" in smoke_job
    assert (
        "uv pip install --python /tmp/smoke-venv/bin/python --overrides "
        "/tmp/sase-overrides.txt dist/*.whl" in smoke_job
    )
    assert smoke_job.index("dist/*.whl") < smoke_job.index("just install-source-sase")
