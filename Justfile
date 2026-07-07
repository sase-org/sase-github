# sase-github task runner

venv_dir := ".venv"
venv_bin := venv_dir / "bin"

default:
    @just --list

_setup:
    @[ -x {{ venv_bin }}/python ] || (uv venv {{ venv_dir }} && just install)

install:
    @[ -x {{ venv_bin }}/python ] || uv venv {{ venv_dir }}
    @if [ -n "${SASE_CORE_PATH:-}" ]; then \
        printf -- '-e %s\n' "$(realpath "${SASE_CORE_PATH}")" > {{ venv_dir }}/sase-overrides.txt; \
        uv pip install --overrides {{ venv_dir }}/sase-overrides.txt -e ".[dev]"; \
    else \
        uv pip install -e ".[dev]"; \
    fi

lint: _setup
    {{ venv_bin }}/ruff check src/ tests/
    {{ venv_bin }}/mypy

fmt: _setup
    {{ venv_bin }}/ruff format src/ tests/
    {{ venv_bin }}/ruff check --fix src/ tests/

test *args: _setup
    {{ venv_bin }}/pytest {{ args }}

check: lint test

clean:
    rm -rf build/ dist/ *.egg-info src/*.egg-info .mypy_cache/ .ruff_cache/ .pytest_cache/

build: _setup
    {{ venv_bin }}/python -m build
