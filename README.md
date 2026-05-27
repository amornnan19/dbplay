# pydbplay

Local-first DB query playground — a lightweight TablePlus alternative that runs on localhost, opened through your browser.

See [SPEC.md](SPEC.md) for the full design document.

## Quick start

```bash
# Install
uv sync

# Dev server (hot reload)
uv run uvicorn pydbplay.app.main:app --reload --host 127.0.0.1 --port 7777

# Production-ish launch
uv run pydbplay start [--port 8080] [--open]
```

## Quality gates

```bash
uv run ruff check . && uv run ruff format .
uv run mypy pydbplay
uv run pytest                          # unit tests (no Docker needed)
uv run pytest -m integration           # adapter tests (requires Docker)
```
