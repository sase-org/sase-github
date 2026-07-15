# sase-github task runner

venv_dir := ".venv"
venv_bin := venv_dir / "bin"

default:
    @just --list

_setup:
    @[ -x {{ venv_bin }}/python ] || (uv venv {{ venv_dir }} && just install)

install-source-sase python:
    @set -eu; \
    sase_python_path="${SASE_PYTHON_PATH:-}"; \
    sase_rust_core_path="${SASE_RUST_CORE_PATH:-}"; \
    if [ -z "$sase_python_path" ] || [ -z "$sase_rust_core_path" ]; then \
        printf '%s\n' \
            'SASE_PYTHON_PATH and SASE_RUST_CORE_PATH must be set together' >&2; \
        exit 2; \
    fi; \
    sase_python_path="$(realpath "$sase_python_path")"; \
    sase_rust_core_path="$(realpath "$sase_rust_core_path")"; \
    python_dir="$(cd "$(dirname "{{ python }}")" && pwd)"; \
    python_path="$python_dir/$(basename "{{ python }}")"; \
    venv_path="$(dirname "$python_dir")"; \
    uv pip install --python "$python_path" -e "$sase_python_path"; \
    uv pip install --python "$python_path" maturin; \
    ( \
        cd "$sase_rust_core_path/crates/sase_core_py"; \
        VIRTUAL_ENV="$venv_path" \
            PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 \
            "$venv_path/bin/maturin" develop --release; \
    ); \
    uv pip install --python "$python_path" --no-deps -e "$sase_python_path"

install:
    @[ -x {{ venv_bin }}/python ] || uv venv {{ venv_dir }}
    @set -eu; \
    sase_python_path="${SASE_PYTHON_PATH:-}"; \
    sase_rust_core_path="${SASE_RUST_CORE_PATH:-}"; \
    if [ -z "$sase_python_path" ] && [ -z "$sase_rust_core_path" ]; then \
        rm -f {{ venv_dir }}/sase-overrides.txt; \
        uv pip install --python {{ venv_bin }}/python -e ".[dev]"; \
    elif [ -z "$sase_python_path" ] || [ -z "$sase_rust_core_path" ]; then \
        printf '%s\n' \
            'SASE_PYTHON_PATH and SASE_RUST_CORE_PATH must be set together' >&2; \
        exit 2; \
    else \
        sase_python_path="$(realpath "$sase_python_path")"; \
        venv_path="$(realpath {{ venv_dir }})"; \
        printf -- '-e %s\n' "$sase_python_path" > "$venv_path/sase-overrides.txt"; \
        uv pip install --python "$venv_path/bin/python" \
            --overrides "$venv_path/sase-overrides.txt" -e ".[dev]"; \
        just install-source-sase "$venv_path/bin/python"; \
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
