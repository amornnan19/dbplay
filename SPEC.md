# pydbplay — Local DB Query Playground

> Local-first DB client ที่รันบนเครื่องตัวเอง ใช้แทน TablePlus สำหรับงานทั่วไป
> เน้น Query editor ที่ดี + Edit row ไหลลื่น + Export ได้

---

## 1. Overview

### Vision
สร้าง DB client แบบเบาๆ รันบน localhost เปิดผ่านเบราว์เซอร์ ไม่ต้องลง Electron app
ใช้ Python ล้วน + HTMX (ไม่มี build step) → setup ง่าย, dev เร็ว, ต่อยอดได้

### Non-Goals
- ไม่ทำเป็น hosted SaaS (local-only เท่านั้น)
- ไม่ทำ visual schema designer / ERD (ใช้ tool อื่นดีกว่า)
- ไม่ทำ migration management (ให้ใช้ alembic / goose / atlas แทน)
- ไม่ทำ replication / DBA-level ops

### Target Users
- Developer ที่ใช้หลาย DB engine แล้วเบื่อจ่ายค่า TablePlus
- คนที่อยากได้ tool ใน workflow ของตัวเอง customize ได้
- ใช้เป็น dev tool ระหว่าง build feature (query → tweak → re-run)

---

## 2. Tech Stack

### Backend
- **Python 3.12+**
- **uv** — package manager (เร็วกว่า poetry, lock file ดี)
- **FastAPI** — HTTP layer
- **SQLAlchemy 2.0 (Core, ไม่ใช่ ORM)** — DB abstraction layer
- **psycopg[binary]** — PostgreSQL driver
- **PyMySQL** — MySQL/MariaDB driver
- **sqlite3** — stdlib
- **sqlglot** — SQL parsing, validation, dialect translation
- **pydantic v2** — request/response models
- **uvicorn** — ASGI server

> **Concurrency model: synchronous.** ทุก driver ที่เลือก (psycopg sync mode, PyMySQL, stdlib sqlite3) เป็น sync และ adapter interface (§5) เป็น sync ทั้งหมด — FastAPI route ที่แตะ DB ให้ประกาศเป็น `def` (ไม่ใช่ `async def`) → Starlette รันใน threadpool ให้เอง เป็น local single-user tool (≤5 connection ต่อ profile) async คือ over-engineering ที่ไม่ได้ประโยชน์จริง และ SQLAlchemy async engine ก็บังคับใช้ async driver (asyncpg/aiomysql/aiosqlite) ซึ่งไม่ตรงกับ driver ข้างบน

### Frontend (no build step)
- **Jinja2** — server-side templating
- **HTMX** — interactive UI ผ่าน HTML attributes
- **Alpine.js** — client-side state ส่วนเล็กๆ (dropdown, modal)
- **Tailwind CSS** — ใช้ CDN ตอน dev, Tailwind CLI standalone ตอน build (ไม่ต้องมี node)
- **CodeMirror 6** — SQL editor (autocomplete, syntax highlight) โหลดจาก CDN

### Storage (app-level, ไม่ใช่ DB ปลายทาง)
- **SQLite** — เก็บ connection profile, query history, saved query
- Path: `~/.pydbplay/app.db`

### Dev tools
- **ruff** — lint + format
- **mypy** — type check
- **pytest** — test (ทุกอย่าง sync ไม่ต้องมี pytest-asyncio)
- **pre-commit** — hook

---

## 3. Project Structure

```
pydbplay/
├── pyproject.toml
├── uv.lock
├── README.md
├── .pre-commit-config.yaml
├── .gitignore
│
├── pydbplay/
│   ├── __init__.py
│   ├── __main__.py              # entry: python -m pydbplay
│   ├── cli.py                   # CLI: pydbplay start / pydbplay init
│   ├── config.py                # app config, paths
│   │
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app factory
│   │   ├── dependencies.py      # DI: get_connection_manager, etc.
│   │   ├── exceptions.py        # custom exceptions + handlers
│   │   │
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── pages.py         # HTML pages (Jinja2)
│   │   │   ├── connections.py   # /api/connections
│   │   │   ├── schema.py        # /api/schema (tables, columns)
│   │   │   ├── query.py         # /api/query (execute SQL)
│   │   │   ├── rows.py          # /api/rows (CRUD on rows)
│   │   │   └── export.py        # /api/export (CSV/JSON/SQL)
│   │   │
│   │   ├── templates/
│   │   │   ├── base.html
│   │   │   ├── partials/        # HTMX swap targets
│   │   │   │   ├── table_list.html
│   │   │   │   ├── result_grid.html
│   │   │   │   ├── row_edit.html
│   │   │   │   └── ...
│   │   │   ├── connections.html
│   │   │   ├── query.html
│   │   │   └── browse.html
│   │   │
│   │   └── static/
│   │       ├── css/
│   │       │   └── app.css      # tailwind output
│   │       ├── js/
│   │       │   ├── editor.js    # CodeMirror init
│   │       │   ├── grid.js      # result grid interactions
│   │       │   └── htmx-ext.js  # custom HTMX extensions
│   │       └── vendor/          # CDN fallbacks (optional)
│   │
│   ├── core/                    # business logic, ไม่พึ่ง FastAPI
│   │   ├── __init__.py
│   │   ├── connection_manager.py
│   │   ├── query_executor.py
│   │   ├── schema_inspector.py
│   │   ├── row_editor.py
│   │   ├── exporter.py
│   │   ├── sql_validator.py     # sqlglot wrapper
│   │   └── history.py
│   │
│   ├── db/                      # app-internal SQLite
│   │   ├── __init__.py
│   │   ├── models.py            # ConnectionProfile, QueryHistory, SavedQuery
│   │   ├── repository.py
│   │   └── migrations/
│   │       ├── 001_init.sql
│   │       └── ...
│   │
│   ├── adapters/                # per-engine adapter
│   │   ├── __init__.py
│   │   ├── base.py              # abstract DBAdapter
│   │   ├── postgres.py
│   │   ├── mysql.py
│   │   └── sqlite.py
│   │
│   └── schemas/                 # pydantic models
│       ├── __init__.py
│       ├── connection.py
│       ├── query.py
│       └── schema.py
│
└── tests/
    ├── conftest.py
    ├── test_query_executor.py
    ├── test_connection_manager.py
    ├── test_adapters/
    └── fixtures/
```

### หลักการแบ่ง layer
- **`app/`** — FastAPI ล้วน (router, template, static)
- **`core/`** — pure Python business logic ไม่รู้จัก FastAPI (test ง่าย)
- **`adapters/`** — รู้จัก DB engine แต่ละตัว แปลงเป็น interface เดียวกัน
- **`db/`** — app's own storage (SQLite) ใช้ **SQLAlchemy Core** (`Table` objects + `insert()/select()`) ไม่ใช่ raw SQL string — เรามี SQLAlchemy เป็น dep อยู่แล้ว ได้ parameterized query ฟรี ลด surface ของ SQL injection ในการเก็บข้อมูลตัวเอง ("no ORM" = ใช้ Core ไม่ได้แปลว่าต้องต่อ string เอง)
- **`schemas/`** — pydantic schemas สำหรับ API contracts

คล้ายๆ handler/service/repository แบบใน Go layered architecture แต่เป็นเวอร์ชั่น Python

---

## 4. Data Models

### App-internal (SQLite, `~/.pydbplay/app.db`)

```python
# pydbplay/db/models.py

class ConnectionProfile(BaseModel):
    id: int
    name: str                    # "Synora HR — local"
    engine: Literal["postgres", "mysql", "sqlite"]
    host: str | None
    port: int | None
    database: str
    username: str | None
    password_encrypted: str | None  # encrypt ด้วย local key
    ssl_mode: str | None
    read_only: bool              # hard guard: block ทุก statement ที่ไม่ใช่ SELECT (Phase 1)
    color: str | None            # hex สำหรับ tab indicator
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None


class QueryHistory(BaseModel):
    id: int
    connection_id: int
    sql: str
    executed_at: datetime
    duration_ms: int
    row_count: int | None
    success: bool
    error_message: str | None


class SavedQuery(BaseModel):
    id: int
    connection_id: int | None    # null = global
    name: str
    sql: str
    description: str | None
    created_at: datetime
    updated_at: datetime
```

### Password Encryption
- **Default: OS keychain ผ่าน `keyring`** — เก็บ password ใน macOS Keychain (หรือ Secret Service บน Linux), `app.db` เก็บแค่ reference ไม่เก็บ secret บนดิสก์เลย
- **Fallback: Fernet** — สำหรับ headless/Linux ที่ไม่มี keychain: generate `~/.pydbplay/secret.key` (Fernet key, `0600`) ตอน first run แล้ว encrypt password ก่อนเก็บ SQLite
- เหตุผลที่ keyring เป็น default: Fernet key file นั่งอยู่ข้างๆ ข้อมูลที่มันเข้ารหัส (ทั้งคู่ใน `~/.pydbplay/`) → ใครอ่าน dir ได้ก็ถอดได้ keychain แยก trust boundary ออกไป
- ถ้า key/keychain entry หาย → ต้องใส่ password ใหม่ทุก connection

---

## 5. Adapter Interface

```python
# pydbplay/adapters/base.py

from abc import ABC, abstractmethod
from typing import Iterator

class DBAdapter(ABC):
    """Interface เดียวกันสำหรับทุก DB engine. Sync ทั้งหมด — route ที่เรียกให้เป็น `def` (รันใน threadpool)."""

    @abstractmethod
    def test_connection(self) -> bool: ...

    @abstractmethod
    def list_schemas(self) -> list[str]: ...

    @abstractmethod
    def list_tables(self, schema: str | None = None) -> list[TableInfo]: ...

    @abstractmethod
    def describe_table(self, table: str, schema: str | None = None) -> TableSchema: ...

    @abstractmethod
    def execute(self, sql: str, params: dict | None = None) -> QueryResult: ...

    @abstractmethod
    def execute_stream(self, sql: str, chunk_size: int = 1000) -> Iterator[list[dict]]:
        """Server-side / chunked cursor — ห้าม fetchall() บนตารางใหญ่."""

    @abstractmethod
    def quote_identifier(self, name: str) -> str:
        """PG: "name"  MySQL: `name`  SQLite: "name" """

    @abstractmethod
    def validate_identifier(self, name: str, *, known: set[str]) -> str:
        """Whitelist กับ schema จริงก่อน quote — กัน identifier injection ใน row editor."""

    @abstractmethod
    def get_pk_columns(self, table: str, schema: str | None = None) -> list[str]: ...
```

แต่ละ engine implement interface นี้ → router ไม่ต้องสน engine

---

## 6. API Endpoints

### Pages (HTML, server-rendered)
| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Redirect ไป `/connections` |
| GET | `/connections` | รายการ connection |
| GET | `/c/{conn_id}` | Dashboard ของ connection (sidebar + query area) |
| GET | `/c/{conn_id}/query` | Query editor |
| GET | `/c/{conn_id}/browse/{table}` | Browse table view |

### API (JSON หรือ HTML partial สำหรับ HTMX)

#### Connections
| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/connections` | — | list[ConnectionProfile] |
| POST | `/api/connections` | ConnectionCreate | ConnectionProfile |
| POST | `/api/connections/test` | ConnectionCreate | {ok, message} |
| PATCH | `/api/connections/{id}` | ConnectionUpdate | ConnectionProfile |
| DELETE | `/api/connections/{id}` | — | 204 |
| POST | `/api/connections/{id}/connect` | — | sets active, returns partial |

#### Schema
| Method | Path | Returns |
|---|---|---|
| GET | `/api/c/{conn_id}/schemas` | list[str] |
| GET | `/api/c/{conn_id}/tables?schema=X` | list[TableInfo] (HTML partial) |
| GET | `/api/c/{conn_id}/tables/{table}` | TableSchema (columns, indexes, FKs) |

#### Query
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/c/{conn_id}/query` | {sql, limit?} | QueryResult (HTML partial) |
| POST | `/api/c/{conn_id}/query/validate` | {sql} | {ok, errors, dialect} |
| GET | `/api/c/{conn_id}/query/history` | — | list[QueryHistory] |
| GET | `/api/c/{conn_id}/query/autocomplete?prefix=X` | — | list[Suggestion] |

#### Rows (Phase 2)
| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/c/{conn_id}/rows/{table}?page=1&filter=...` | — | paginated rows |
| PATCH | `/api/c/{conn_id}/rows/{table}/{pk}` | {column: value} | updated row |
| POST | `/api/c/{conn_id}/rows/{table}` | row data | inserted row |
| DELETE | `/api/c/{conn_id}/rows/{table}/{pk}` | — | 204 |

#### Export
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/c/{conn_id}/export?format=csv\|json\|sql` | {sql} or {table} | file stream |

---

## 7. Frontend Patterns

### HTMX patterns ที่ใช้บ่อย

**1. Run query → swap result panel**
```html
<form hx-post="/api/c/{{ conn.id }}/query"
      hx-target="#result-panel"
      hx-swap="innerHTML"
      hx-indicator="#query-loading">
  <textarea name="sql" id="sql-editor"></textarea>
  <button type="submit">Run (⌘+Enter)</button>
</form>

<div id="result-panel"><!-- swapped here --></div>
```

**2. Edit row inline → swap row only**
```html
<tr hx-target="this" hx-swap="outerHTML">
  <td>{{ row.id }}</td>
  <td>
    <span hx-get="/api/c/{{ conn.id }}/rows/users/{{ row.id }}/edit?col=name"
          hx-trigger="dblclick">
      {{ row.name }}
    </span>
  </td>
</tr>
```

**3. Sidebar table list → lazy load**
```html
<div hx-get="/api/c/{{ conn.id }}/tables?schema=public"
     hx-trigger="load"
     hx-swap="innerHTML">
  <span class="loading">Loading tables…</span>
</div>
```

### CodeMirror 6 integration

```javascript
// static/js/editor.js
import { EditorView, basicSetup } from "https://esm.sh/codemirror@6";
import { sql, PostgreSQL, MySQL, SQLite } from "https://esm.sh/@codemirror/lang-sql@6";

const DIALECTS = { postgres: PostgreSQL, mysql: MySQL, sqlite: SQLite };

window.initEditor = function(elementId, dialect, initialValue) {
  const view = new EditorView({
    doc: initialValue || "",
    extensions: [
      basicSetup,
      // autocomplete source of truth = server endpoint /autocomplete?prefix=X (ดู §9)
      // preload schema ลง window ได้เฉพาะ schema เล็ก; schema ใหญ่ให้พึ่ง server completion
      sql({ dialect: DIALECTS[dialect], schema: window.__schemaForAutocomplete }),
      EditorView.theme({ "&": { height: "300px" } }),
    ],
    parent: document.getElementById(elementId),
  });

  // expose สำหรับ form submit
  document.getElementById(elementId).__getValue = () => view.state.doc.toString();
  return view;
};
```

ก่อน submit form → grab value จาก CodeMirror ใส่ hidden input

### Keybindings
- `⌘+Enter` / `Ctrl+Enter` — Run query
- `⌘+S` / `Ctrl+S` — Save query
- `⌘+K` / `Ctrl+K` — Command palette (Phase 3)
- `⌘+/` / `Ctrl+/` — Toggle comment

---

## 8. Roadmap

จัด phase ตาม priority ที่เลือกไว้: query editor → browse/edit → export → multi-connection

### Phase 1: Query Editor (Week 1-2) — MVP
**เป้าหมาย:** เปิด app → ต่อ DB → รัน query → ดูผล

- [ ] Project scaffold (uv, FastAPI, Jinja2, Tailwind standalone)
- [ ] App-internal SQLite schema + repository
- [ ] Connection management UI (CRUD)
  - [ ] Add/edit/delete connection profile
  - [ ] Test connection button
  - [ ] Password encryption (Fernet)
- [ ] PostgreSQL adapter
- [ ] MySQL adapter
- [ ] SQLite adapter
- [ ] Query page layout (sidebar + editor + result panel)
- [ ] CodeMirror 6 SQL editor พร้อม dialect switching
- [ ] Execute query endpoint
- [ ] Result grid (HTML table with virtual scroll สำหรับ row เยอะ)
- [ ] Schema autocomplete (table + column names)
- [ ] Query history (auto-save, sidebar dropdown)
- [ ] ⌘+Enter keybinding
- [ ] Error display (SQL syntax errors highlighted in editor)
- [ ] **Read-only mode flag ต่อ connection** (hard guard ที่ชั้น adapter: block non-SELECT) — ย้ายจาก Phase 5 ขึ้นมาเพราะ use case หลักคือต่อ prod
- [ ] **Bind `127.0.0.1` เท่านั้น + เช็ค Host/Origin header** (กัน DNS-rebinding / LAN exposure — tool นี้ถือ prod creds)

**Deliverable:** ใช้แทน TablePlus ในงาน "เปิดมา query ดูข้อมูล" ได้

### Phase 2: Browse & Edit (Week 3-4)
**เป้าหมาย:** Browse table แบบ TablePlus, double-click cell แก้ได้

- [ ] Table list ใน sidebar (group by schema)
- [ ] Table browse view (paginated)
- [ ] Column filter (per-column WHERE input)
- [ ] Sort by column (click header)
- [ ] Double-click cell → inline edit
- [ ] PATCH row endpoint (ต้องมี PK)
- [ ] Insert new row (form modal)
- [ ] Delete row (with confirm)
- [ ] Table structure tab (columns, indexes, FKs, DDL view)
- [ ] Optimistic UI update + rollback ถ้า error
- [ ] Validation: type check ก่อน submit (เช่น int column → reject "abc")
- [ ] NULL vs empty-string ใน inline edit (UI ต้องแยก `NULL` ออกจาก `''` ชัดเจน)
- [ ] Identifier whitelist: table/column/schema ทุกตัว validate กับ schema จริงก่อนประกอบ SQL (value ใช้ parameterized เสมอ)

**Deliverable:** ทำงาน CRUD บน table ได้โดยไม่ต้องเขียน SQL

### Phase 3: Export (Week 5)
- [ ] Export query result → CSV (streaming, รองรับไฟล์ใหญ่)
- [ ] Export query result → JSON
- [ ] Export query result → SQL INSERT statements
- [ ] Export entire table (same formats)
- [ ] Schema-only export (DDL)
- [ ] Copy result → clipboard (CSV, TSV, JSON)
- [ ] Import CSV → table (Phase 3.5, ถ้ามีเวลา)

### Phase 4: Multi-Connection UX (Week 6)
- [ ] Tab bar ด้านบน (เปิดได้หลาย connection พร้อมกัน)
- [ ] Color-coded tabs ตาม `ConnectionProfile.color`
- [ ] State preservation per tab (query text, result, scroll)
- [ ] Quick switch keybinding (⌘+1, ⌘+2, …)
- [ ] Recent connections menu

### Phase 5: Polish & Nice-to-haves
- [ ] Saved queries (library + folder)
- [ ] Command palette (⌘+K)
- [ ] Dark mode toggle
- [ ] EXPLAIN / EXPLAIN ANALYZE viewer (formatted)
- [ ] SSH tunnel support (paramiko / sshtunnel)
- [ ] Query result diff (เปรียบเทียบ 2 run)
- [ ] Charts (recharts via CDN — Phase 5 จริงๆ)

### Out of scope (อย่างน้อยใน v1)
- ERD / schema diagram
- Visual query builder
- Multi-user / sharing
- Migration management
- Stored procedure debugger

---

## 9. Implementation Notes

### Why no ORM
ใช้ SQLAlchemy **Core** (ไม่ใช่ ORM) เพราะ:
- ผู้ใช้จะรัน raw SQL อยู่แล้ว ORM ไม่ช่วยอะไร
- ต้องการเข้าถึง low-level cursor (เพื่อ stream result ใหญ่ๆ)
- column type → Python type mapping ต้องคุมเอง

หมายเหตุ: **app-internal DB (`db/`) ก็ใช้ Core** — `Table` objects + `insert()/select()` ไม่ใช่ raw SQL string "no ORM" หมายถึงไม่ใช้ ORM session/mapper ไม่ได้แปลว่าต้องต่อ SQL string เอง (Core ให้ parameterized query + กัน injection ในข้อมูลของเราเอง)

### Streaming large results
ห้าม `fetchall()` กับตารางใหญ่ → ใช้ server-side cursor / chunked iterate ผ่าน sync generator + FastAPI `StreamingResponse`
- Default limit: `LIMIT 1000` auto-append ถ้าไม่มี LIMIT
- ถ้า user เอา LIMIT ออกเอง → confirm dialog "result อาจใหญ่มาก ต่อไป?"

### SQL validation flow
```python
# core/sql_validator.py
import sqlglot

def validate(sql: str, dialect: str) -> ValidationResult:
    try:
        parsed = sqlglot.parse(sql, dialect=dialect)
        is_destructive = any(
            stmt and stmt.key.upper() in {"DELETE", "UPDATE", "DROP", "TRUNCATE"}
            for stmt in parsed
        )
        return ValidationResult(ok=True, is_destructive=is_destructive)
    except sqlglot.errors.ParseError as e:
        return ValidationResult(ok=False, error=str(e), line=e.line, col=e.col)
```

destructive query → frontend ขึ้น confirm dialog ก่อน execute

### Connection pooling
- Connection pool per `ConnectionProfile`: max 5 connection
- Idle timeout: 5 นาที → close
- Lazy: เปิด connection ตอน first query เท่านั้น

### Transactions
- Query editor: toggle **auto-commit** (default on สำหรับ SELECT-heavy workflow) — statement ที่เขียน (UPDATE/INSERT/DELETE) ห่อ transaction + ปุ่ม COMMIT/ROLLBACK ก่อน finalize
- Row editor (Phase 2): ทุก PATCH/INSERT/DELETE ห่อ transaction เดียว — error = rollback อัตโนมัติ (คู่กับ optimistic UI rollback)

### Identifier safety (กัน SQL injection)
table/column/schema name **bind เป็น parameter ไม่ได้** (parameterize ได้แค่ value) — quote อย่างเดียวยังไม่ปลอดภัย ดังนั้น row editor / browse ทุกที่ที่ประกอบ identifier เข้า SQL ต้อง:
1. **Whitelist** ชื่อกับ schema จริง (จาก `describe_table`) ก่อน — ปฏิเสธถ้าไม่ตรง
2. แล้วค่อย `quote_identifier`
value ทุกตัวใช้ parameterized query เสมอ

### Read-only enforcement
`ConnectionProfile.read_only=True` → adapter `execute()` parse statement ด้วย sqlglot ถ้าไม่ใช่ SELECT/EXPLAIN → reject ก่อนส่งถึง DB (กันที่ชั้น adapter ไม่ใช่แค่ UI)

### Type handling (เคสที่พัง `str()` ตรงๆ)
ต้อง round-trip (อ่าน → แสดง → edit → เขียนกลับ) ให้ถูกสำหรับ: timestamp/timezone, JSON/JSONB, Postgres array, UUID, NUMERIC/Decimal (อย่าแปลงเป็น float), bytea/blob (แสดงเป็น hex / `<binary>`), enum, NULL (ต้องต่างจาก `''` — ดู Phase 2)

### Security / network binding
- **Bind `127.0.0.1` เท่านั้นเป็น default** (อย่า `0.0.0.0`) — tool นี้ถือ prod credentials การหลุดออก LAN = ใครก็ยิง SQL เข้า DB ได้
- เช็ค `Host`/`Origin` header กัน **DNS-rebinding** (เว็บมุ่งร้าย resolve domain มาที่ `127.0.0.1:7777` แล้วยิง request) + ใส่ **CSRF token** บน mutating endpoint

### Multi-statement
นโยบาย: **หนึ่ง statement ต่อหนึ่ง run** — ใช้ sqlglot split ถ้าเจอหลาย statement ให้ reject หรือถามก่อน (psycopg กับ PyMySQL handle multi-statement ต่างกัน ไม่อยากพึ่ง behavior นั้น)

### Migration runner (app DB)
`db/migrations/*.sql` track ด้วย `PRAGMA user_version` ของ SQLite — ตอน start เทียบ version ปัจจุบันกับไฟล์ แล้ว apply ที่ใหม่กว่าตามลำดับ (runner เบาๆ ไม่ต้องใช้ alembic)

### Autocomplete (source of truth เดียว)
ใช้ **server endpoint `/autocomplete?prefix=X` เป็นหลัก** (รองรับ schema ใหญ่, lazy) — ตัด client-side preload schema ทั้งก้อนลง `window.__schemaForAutocomplete` ออก เว้นแต่ schema เล็กพอจะ cache ทั้งก้อนได้

### Hot reload (dev mode)
```bash
uv run uvicorn pydbplay.app.main:app --reload --host 127.0.0.1 --port 7777
```

### Production-ish launch (สำหรับใช้จริง)
```bash
pydbplay start              # default port 7777
pydbplay start --port 8080
pydbplay start --open       # auto open browser
```

`pydbplay start` ใน CLI = uvicorn (bind `127.0.0.1`) + auto-open browser + เช็คว่า port ว่าง

### Testing strategy
- **Adapter tests**: ใช้ testcontainers รัน PG/MySQL จริงในตัว test — mark `@pytest.mark.integration` (ต้องมี Docker) แยกจาก unit suite ที่รันได้โดยไม่ต้องมี Docker
- **SQLite**: ใช้ in-memory `:memory:` ตรงๆ
- **Router tests**: TestClient ของ FastAPI (sync)
- **Core logic**: pure pytest, mock adapter
- ทุกอย่าง sync → ไม่ต้องใช้ pytest-asyncio
- เป้าหมาย coverage: core/ > 80%, adapter/ > 70%

---

## 10. Quick Start (สำหรับ Claude Code)

```bash
# init
mkdir pydbplay && cd pydbplay
uv init --python 3.12
uv add fastapi 'uvicorn[standard]' jinja2 sqlalchemy psycopg[binary] \
       pymysql sqlglot pydantic 'pydantic-settings' keyring cryptography
uv add --dev ruff mypy pytest testcontainers httpx

# folder
mkdir -p pydbplay/{app/{routers,templates/partials,static/{css,js}},core,db/migrations,adapters,schemas}
mkdir -p tests/{fixtures,test_adapters}

# start (bind 127.0.0.1 เท่านั้น — ดู §9 Security)
uv run uvicorn pydbplay.app.main:app --reload --host 127.0.0.1 --port 7777
```

### Order ของการ implement
1. `pydbplay/db/` — app-internal storage ก่อน (CRUD ConnectionProfile)
2. `pydbplay/adapters/sqlite.py` — adapter ง่ายสุด เอามาทดสอบ interface
3. `pydbplay/core/connection_manager.py`
4. `pydbplay/app/main.py` + `routers/connections.py`
5. `pydbplay/app/templates/` — connection list page
6. `adapters/postgres.py`, `adapters/mysql.py`
7. `core/query_executor.py` + `routers/query.py`
8. `templates/query.html` + CodeMirror
9. ที่เหลือตาม Phase

---

## 11. Open Questions

### เคาะแล้ว (2026-05-27)
- **Sync ทั้งหมด** (ไม่ใช่ async) — driver ที่เลือกเป็น sync, local single-user ไม่ต้องการ async (ดู §2, §5)
- **Read-only mode → Phase 1** + เก็บเป็น flag ต่อ connection (use case หลักคือต่อ prod)
- **Password → `keyring` (OS keychain) เป็น default**, Fernet เป็น fallback
- **App DB ใช้ SQLAlchemy Core** ไม่ใช่ raw SQL string

### เคาะเพิ่ม (2026-05-27)
- **SSH tunnel — ไม่อยู่ใน v1** คงไว้ Phase 5 (MVP คือ local query editor) ระหว่างนี้ใช้ `ssh -L` port-forward ภายนอก app ต่อ prod ได้
- **Port default = 7777** คงไว้ (จำง่าย ชนยาก) เปลี่ยนได้ด้วย `--port`
- **CDN vs vendored: dev ใช้ CDN, packaging vendor** — ตอน ship ดึง asset (HTMX/Alpine/CodeMirror/Tailwind) มา vendor + pin version เพื่อให้ทำงาน offline และไม่พังเมื่อ CDN เปลี่ยน (เป็น step ใน Phase packaging)

ตอนนี้ไม่มีคำถามค้างแล้ว — พร้อม implement