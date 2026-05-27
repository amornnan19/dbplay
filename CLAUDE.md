# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status: pre-implementation

This repo currently contains **only `SPEC.md`** — no code, no `pyproject.toml`, no git history. `SPEC.md` is the authoritative design document for **pydbplay**, a local-first DB query playground (a lightweight TablePlus alternative that runs on localhost and is used through the browser). Read `SPEC.md` before doing anything; everything below summarizes it and flags the parts most likely to be missed.

When scaffolding, follow `SPEC.md` §10 (Quick Start) for the exact `uv` commands and the intended implementation order, and §3 for the target directory layout. Treat `SPEC.md` as the spec — if you deviate from it, say so explicitly.

## Tech stack (per SPEC.md)

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy 2.0 **Core (not ORM)**, Pydantic v2, uvicorn. Managed with **uv** (not pip/poetry). **Synchronous throughout** — the chosen drivers are sync, so DB-touching routes are `def` (not `async def`) and Starlette runs them in a threadpool. Do not reintroduce an async engine/drivers.
- **DB drivers:** `psycopg[binary]` (Postgres), `PyMySQL` (MySQL/MariaDB), stdlib `sqlite3`.
- **SQL parsing:** `sqlglot` (validation, dialect detection, destructive-statement detection).
- **Frontend (no build step):** Jinja2 server-rendered templates + HTMX + Alpine.js + Tailwind (CDN in dev, standalone CLI for packaging) + CodeMirror 6 from CDN.
- **App-internal storage:** SQLite at `~/.pydbplay/app.db` (connection profiles, query history, saved queries) — distinct from the user's target databases. Accessed via SQLAlchemy **Core** `Table` objects (not raw SQL). Connection passwords live in the OS keychain via `keyring`, with a Fernet key-file fallback.
- **Dev tools:** ruff (lint+format), mypy, pytest (sync — no pytest-asyncio), pre-commit.

## Commands (intended — verify against `pyproject.toml` once scaffolded)

```bash
# Scaffold (run once, see SPEC.md §10)
uv init --python 3.12
uv add fastapi 'uvicorn[standard]' jinja2 sqlalchemy psycopg[binary] \
       pymysql sqlglot pydantic 'pydantic-settings' keyring cryptography
uv add --dev ruff mypy pytest testcontainers httpx

# Dev server (hot reload) — bind localhost only
uv run uvicorn pydbplay.app.main:app --reload --host 127.0.0.1 --port 7777

# Production-ish launch (CLI: uvicorn + port check + auto-open browser)
pydbplay start [--port 8080] [--open]

# Quality gates
uv run ruff check . && uv run ruff format .
uv run mypy pydbplay
uv run pytest                          # all tests
uv run pytest tests/test_query_executor.py::test_name   # single test
```

## Architecture: strict layering

The whole point of the directory split is dependency direction. Keep it strict — it's what makes the core testable and the engines swappable.

- **`app/`** — FastAPI only: routers, Jinja2 templates, static assets. Routers stay thin; they call `core/`.
- **`core/`** — pure Python business logic that **must not import FastAPI** (connection_manager, query_executor, schema_inspector, row_editor, exporter, sql_validator, history). This is where the testable logic lives.
- **`adapters/`** — one module per DB engine (postgres/mysql/sqlite), each implementing the abstract `DBAdapter` interface in `adapters/base.py`. **Routers and core must never branch on engine type** — they depend only on the `DBAdapter` interface, so engine differences (identifier quoting, PK discovery, schema listing) are isolated here.
- **`db/`** — the app's own SQLite storage via SQLAlchemy **Core** `Table` defs (models, repository, `migrations/*.sql` applied by a `PRAGMA user_version` runner — no Alembic). Not the user's target DBs.
- **`schemas/`** — Pydantic v2 models that define API contracts.

Mental model: handler (`app`) → service (`core`) → repository/engine (`db`/`adapters`), Go-style layering in Python.

## Implementation order (SPEC.md §10)

Build bottom-up so each layer can be exercised before the next: `db/` (app storage + ConnectionProfile CRUD) → `adapters/sqlite.py` (simplest, validates the `DBAdapter` interface) → `core/connection_manager.py` → `app/main.py` + `routers/connections.py` → connection-list template → `adapters/postgres.py` + `adapters/mysql.py` → `core/query_executor.py` + `routers/query.py` → query template + CodeMirror → remaining phases. Phases 1–5 are detailed in SPEC.md §8; Phase 1 (Query Editor) is the MVP.

## Non-obvious constraints (read SPEC.md §9 before implementing these)

- **No ORM, by design.** Use SQLAlchemy Core for raw-SQL access and low-level cursors. Don't introduce the ORM layer.
- **Never `fetchall()` on user tables.** Large results must stream via server-side / chunked cursors (`execute_stream`, default `chunk_size=1000`). Auto-append `LIMIT 1000` when the user's SQL has no LIMIT; if they remove it, the frontend confirms before running.
- **Destructive-query guard.** `core/sql_validator.py` uses `sqlglot.parse` to detect DELETE/UPDATE/DROP/TRUNCATE and flags `is_destructive`; the frontend must show a confirm dialog before executing those. Parse errors return line/col for editor highlighting.
- **Identifier injection.** Table/column/schema names can't be parameterized — in the row editor/browse, whitelist every identifier against the live schema (`describe_table`) **before** quoting; only values go through parameterized queries. Quoting alone is not sufficient.
- **Read-only connections (Phase 1).** `ConnectionProfile.read_only` is enforced inside the adapter `execute()` (reject non-SELECT/EXPLAIN before it reaches the DB), not just in the UI — the stated use case is connecting to prod.
- **Bind localhost only.** Serve on `127.0.0.1` (never `0.0.0.0`); validate `Host`/`Origin` against DNS-rebinding and require a CSRF token on mutating endpoints — this tool holds prod credentials.
- **Transactions.** Wrap written statements (UPDATE/INSERT/DELETE) and all row edits in a transaction with explicit commit/rollback; pair adapter rollback with the optimistic-UI rollback.
- **Password storage.** Default to the OS keychain via `keyring` (`app.db` keeps only a reference); Fernet (`~/.pydbplay/secret.key`, `0600`) is the headless/Linux fallback. Never store plaintext passwords.
- **Connection pooling.** Per `ConnectionProfile`: max 5 connections, lazy-opened on first query, idle timeout 5 min.
- **HTMX contract.** Most `/api/...` endpoints return **HTML partials** for HTMX swaps, not JSON — check the "Returns" column in SPEC.md §6 per endpoint. CodeMirror holds the SQL editor state; grab its value into a hidden input before form submit (SPEC.md §7).

## Testing strategy (SPEC.md §9)

- Adapter tests use **testcontainers** to run real Postgres/MySQL (needs Docker; mark `@pytest.mark.integration` so the unit suite runs without it); SQLite uses in-memory `:memory:`.
- Core logic: plain pytest with a mocked adapter. Router tests: FastAPI `TestClient`.
- Coverage targets: `core/` > 80%, `adapters/` > 70%.

## Open questions (SPEC.md §11)

All resolved 2026-05-27 (see SPEC.md §11): sync over async; read-only in Phase 1 as a per-connection flag; `keyring` default for passwords; app DB on SQLAlchemy Core; **SSH tunnel deferred to Phase 5** (use an external `ssh -L` port-forward meanwhile); **default port 7777** (override with `--port`); **assets via CDN in dev, vendored + version-pinned at packaging** for offline-capable shipping. No open product questions remain.
