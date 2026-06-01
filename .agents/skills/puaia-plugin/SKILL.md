---
name: puaia-plugin
description: "Step-by-step guide for creating a PuAiA plugin: repository layout, pyproject.toml conventions, config.toml schema, PuAiAPlugin ABC reference, lifecycle API workflow, and testing patterns."
user-invocable: true
disable-model-invocation: false
version: 1.0.0
category: guide
author: Markus Merzinger
license: MIT
progressive_disclosure:
  entry_point:
    summary: "Create a PuAiA plugin by implementing the PuAiAPlugin ABC in a Git repository and registering it via POST /plugins"
    when_to_use: "When building a new plugin that extends PuAiA with a custom RAG data source, system prompt, retrieval strategy, or document normalisation"
    quick_start: |
      1. Create a Python package with a class that extends PuAiAPlugin
      2. Declare [tool.puaia] plugin = "my_package.MyPlugin" in pyproject.toml
      3. Optionally declare config fields in config.toml
      4. Push to a Git repo
      5. POST /plugins {"gitUrl": "https://..."} to register
  token_estimate:
    entry: 150
    full: 4500
context_limit: 800
tags:
  - puaia
  - plugin
  - rag
  - python
  - dynamic-loading
requires_tools: []
---

# PuAiA Plugin Authoring Guide

## Overview

A **PuAiA plugin** is a Python class that extends `PuAiAPlugin` (an abstract base class). It acts as the integration point between a data source and the PuAiA RAG pipeline. Every plugin controls:

- How documents are chunked and normalised before storage
- How the vector store is queried (similarity threshold, hybrid search ratio, limits)
- How retrieved documents are formatted for the LLM
- What system prompt the LLM receives
- Whether documents are filtered after retrieval
- Optional skills contributed to the global `SkillRegistry`
- Optional post-processing of the LLM response

Plugins can be **built-in** (registered in `app/__init__.py` lifespan) or **dynamic** (loaded at runtime from a Git URL via `POST /plugins`). This guide focuses on dynamic plugins.

---

## Repository Structure

```
my-puaia-plugin/
├── pyproject.toml        # required — Python package metadata + PuAiA entry point
├── config.toml           # optional — declares operator-supplied config fields
└── my_package/
    ├── __init__.py
    └── plugin.py         # the PuAiAPlugin subclass
```

The repository must be a valid Python package installable via `pip install -e .`.

---

## `pyproject.toml`

### Minimal example

```toml
[project]
name = "my-puaia-plugin"
version = "0.1.0"
description = "My custom PuAiA plugin"
requires-python = ">=3.11"
dependencies = [
    # list any extra runtime dependencies here
    # "httpx>=0.27",
]

# --- Primary discovery mechanism (preferred) ---
[tool.puaia]
plugin = "my_package.plugin.MyPlugin"
# Dotted import path: "<module>.<ClassName>"
# The loader imports this path directly after `uv pip install -e .`

# --- Fallback discovery mechanism ---
[project.entry-points."puaia.plugins"]
my_plugin = "my_package.plugin:MyPlugin"
# Group must be exactly "puaia.plugins"
# The loader tries this if [tool.puaia] plugin is absent or fails

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Discovery order
1. `pyproject.toml` → `[tool.puaia] plugin = "dotted.path.ClassName"` — tried first.
2. `importlib.metadata` entry points in group `puaia.plugins` — tried if step 1 fails.

Both require `uv pip install -e .` to have been run against the repository.

### Adding Python dependencies

List all extra packages in `[project] dependencies`. They are installed automatically when the plugin is loaded:

```toml
[project]
dependencies = [
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "pydantic>=2.0",
]
```

---

## `config.toml`

Declare operator-supplied configuration (API keys, URLs, options). If this file is absent the plugin is activated immediately after installation. If it is present and contains required fields, the plugin enters `PENDING_CONFIG` state until all required values are provided via `PATCH /plugins/{name}/config`.

### Format

```toml
[config]
  [[config.fields]]
  name        = "api_key"
  type        = "string"
  required    = true
  description = "API key for the external service"

  [[config.fields]]
  name        = "base_url"
  type        = "string"
  required    = true
  description = "Base URL of the external API"

  [[config.fields]]
  name        = "max_results"
  type        = "integer"
  required    = false
  default     = 10
  description = "Maximum number of results to return per query"

  [[config.fields]]
  name        = "verify_ssl"
  type        = "boolean"
  required    = false
  default     = true
  description = "Whether to verify SSL certificates"
```

### Supported field types

| Type | Python equivalent |
|---|---|
| `"string"` | `str` |
| `"integer"` | `int` |
| `"boolean"` | `bool` |
| `"float"` | `float` |

### Secret masking

Fields whose `name` contains any of the following substrings (case-insensitive) are automatically masked as `"***"` in `GET /plugins/{name}/config` responses:

`key`, `secret`, `token`, `password`, `credential`, `auth`

The actual values are stored in the database and injected into `__init__` unmasked.

### Accessing config values in your plugin

Config values are passed as a `dict` to `__init__` and stored on `self.config`:

```python
def __init__(self, config=None):
    super().__init__(config)
    self.api_key = self.config.get("api_key", "")
    self.base_url = self.config.get("base_url", "https://api.example.com")
    self.max_results = self.config.get("max_results", 10)
```

---

### Scheduling via `config.toml`

You can also declare cron schedules in `config.toml` instead of (or in addition to) overriding `get_cron_schedule()`. This is useful when you want operators to control the schedule without touching Python code.

```toml
[config]
  [[config.fields]]
  name = "api_key"
  type = "string"
  required = true

[schedule]
cron = ["0 9 * * *", "30 17 * * 1-5"]
```

**Precedence rules:**
1. If `get_cron_schedule()` returns a non-empty list, those expressions are used and `config.toml` is ignored.
2. If `get_cron_schedule()` returns `[]`, the scheduler falls back to `[schedule].cron` in `config.toml`.
3. If both are absent or empty, the plugin has no scheduled tasks.

---

## `PuAiAPlugin` ABC Reference

Located at `app/services/puaia/plugin/puaia_plugin.py`.

### `__init__`

```python
def __init__(self, config: Optional[dict[str, Any]] = None):
    self.config: dict[str, Any] = config or {}
```

Always call `super().__init__(config)` from your subclass `__init__`. `self.config` is then available on the instance.

---

### Abstract methods (must implement)

#### `get_name() -> str`

Returns the unique string key used to identify the plugin throughout the system. This key is used as the `plugin_name` column in the `documents` table and in all API calls.

```python
def get_name(self) -> str:
    return "my_plugin"   # must be unique across all registered plugins
```

**Rules:**
- Lowercase, no spaces (use `_` or `-`)
- Must be stable — changing it orphans existing stored documents
- Used as the URL path segment in all plugin-scoped API calls

---

#### `doc_to_text(doc: DocumentWithScore) -> str`

Converts a retrieved document into the text string that is appended to the LLM prompt context. Called once per retrieved document on every `ask` request.

```python
from app.models.db.document_with_score import DocumentWithScore

def doc_to_text(self, doc: DocumentWithScore) -> str:
    document = doc.document          # LangChain Document
    metadata = document.metadata     # dict — whatever was stored
    content  = document.page_content # the raw text chunk
    score    = doc.score             # float similarity score

    return f"[score={score:.2f}]\n{content}\n---"
```

`DocumentWithScore` fields:
- `doc.document` — `langchain_core.documents.Document`
  - `doc.document.page_content` — `str`, the chunk text
  - `doc.document.metadata` — `dict`, stored metadata
- `doc.score` — `float`, similarity score (higher = more similar)

---

#### `get_vector_store_retrieval_config() -> VectorStoreRetrievalConfig`

Returns the retrieval configuration for this plugin's vector store queries.

```python
from app.services.puaia.vector_store.vector_store_retrieval_config import VectorStoreRetrievalConfig

def get_vector_store_retrieval_config(self) -> VectorStoreRetrievalConfig:
    return VectorStoreRetrievalConfig(
        similarity_score_limit=0.90,   # only return docs above this threshold
        documents_limit=50,            # max docs to retrieve
        hybrid_search_alpha=0.5,       # 0.0=vector only, 1.0=text only, 0.5=balanced
    )
```

`VectorStoreRetrievalConfig` defaults:

| Field | Default | Description |
|---|---|---|
| `similarity_score_limit` | `0.95` | Minimum similarity score to include a document |
| `documents_limit` | `100` | Maximum documents to retrieve |
| `embedding_model` | `"google/gemini-embedding-001"` | Embedding model used for query vector |
| `hybrid_search_alpha` | `0.5` | Balance between vector (0.0) and full-text (1.0) search |
| `text_score_boost` | `10.0` | Amplification factor for full-text score normalisation |
| `order_by` | `None` | Optional SQL column to order results by |

---

### Optional methods (override as needed)

#### `filter_documents(documents: list[DocumentWithScore]) -> list[DocumentWithScore]`

Post-retrieval filtering before documents are passed to `doc_to_text`. Default returns all documents unchanged.

```python
def filter_documents(self, documents):
    # Example: exclude archived documents
    return [d for d in documents if d.document.metadata.get("archived") != "true"]
```

---

#### `get_system_prompt() -> str`

Returns the system prompt injected into every LLM call for this plugin. Default returns `""` (no custom system prompt).

```python
def get_system_prompt(self) -> str:
    return (
        "You are an assistant specialised in answering questions about "
        "my knowledge base. Always answer in the language of the question."
    )
```

Dynamic values (e.g. current date) can be interpolated here:

```python
from datetime import datetime

def get_system_prompt(self) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"Today is {now}. Answer questions about the knowledge base."
```

---

#### `get_text_splitter() -> TextSplitter`

Returns the LangChain `TextSplitter` used to chunk documents on `store`. Default is `RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)`.

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

def get_text_splitter(self):
    return RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", " "],
    )
```

---

#### `normalize_document(text: str) -> str`

Converts raw document text into a search-friendly version stored in the `document_normalized` column, which drives full-text search indexing and embedding. Default returns the text unchanged.

```python
def normalize_document(self, text: str) -> str:
    # Example: strip HTML tags before indexing
    import re
    return re.sub(r"<[^>]+>", "", text).strip()
```

---

#### `get_skills() -> list[Skill]`

Returns a list of `Skill` objects contributed to the global `SkillRegistry` at startup. Skills are automatically matched against user questions and appended to the system prompt when relevant. Default returns `[]`.

```python
from app.services.puaia.skill_registry import Skill

def get_skills(self) -> list[Skill]:
    return [
        Skill(
            name="my_skill",
            keywords=["project", "ticket", "issue"],
            content="When asked about projects, always include the ticket ID.",
        )
    ]
```

---

#### `post_process_response(response_content: str) -> str`

Transforms the final LLM response string before it is streamed to the client. Default returns the content unchanged.

```python
def post_process_response(self, response_content: str) -> str:
    # Example: append a disclaimer
    return response_content + "\n\n_Source: My Knowledge Base_"
```

---

#### `get_cron_schedule() -> list[str]`

Returns a list of cron expressions that drive when the plugin's scheduled task runs. Default returns `[]` (no scheduled runs).

Each expression must be a valid 5-field cron string:

```python
def get_cron_schedule(self) -> list[str]:
    return [
        "0 9 * * *",      # every day at 09:00
        "30 17 * * 1-5",  # every weekday at 17:30
    ]
```

**Resolution order:** `get_cron_schedule()` takes precedence. If it returns an empty list, the scheduler falls back to the `[schedule]` section in `config.toml`.

---

#### `run_scheduled(db_engine: AsyncEngine, ctx: ScheduledTaskContext) -> None`

Called by the global scheduler whenever a cron trigger fires. Use this for periodic background work such as re-indexing, fetching external data, or refreshing caches.

The scheduler passes a ``ScheduledTaskContext`` that exposes ``store``, ``ask``, ``query_scores``, and ``ask_with_context``.  The plugin never holds a reference to ``PuAiAService`` directly.

```python
from sqlalchemy.ext.asyncio import AsyncEngine

async def run_scheduled(self, db_engine: AsyncEngine, ctx: ScheduledTaskContext) -> None:
    """Sync the latest tickets from the external API every hour."""
    import httpx
    from app.models.puaia import StoreRequest

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{self.base_url}/tickets",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        resp.raise_for_status()
        tickets = resp.json()

    # Ingest each ticket into the vector store
    for ticket in tickets:
        await ctx.store(
            plugin_name=self.get_name(),
            request=StoreRequest(
                text=ticket["body"],
                metadata={"ticket_id": ticket["id"], "source": "jira"},
            ),
        )

    logger.info(f"Fetched and stored {len(tickets)} tickets")
```

**Using `ask()` from a scheduled task:**

```python
from app.models.puaia import AskRequest

async def run_scheduled(self, db_engine: AsyncEngine, ctx: ScheduledTaskContext) -> None:
    """Generate a daily summary via the LLM."""
    request = AskRequest(question="Summarise today's new documents")
    summary = await ctx.ask(plugin_name=self.get_name(), request=request)
    logger.info(f"Daily summary: {summary}")
```

**``ScheduledTaskContext`` API:**

| Method | Signature | Description |
|---|---|---|
| ``store`` | ``async def store(plugin_name, request: StoreRequest) -> StoreResponse`` | Store a document into the vector store |
| ``ask`` | ``async def ask(plugin_name, request: AskRequest) -> str`` | Ask a question and return the **collected** response string |
| ``query_scores`` | ``async def query_scores(query, plugin_name, result_limit=None, score_limit=None) -> QueryResponse`` | Query similarity scores |
| ``ask_with_context`` | ``async def ask_with_context(prompt, plugin_name, context=None, system_prompt=None, conversation_id=None) -> str`` | Ask with an explicit context string |

**Scheduler behaviour:**

| Scenario | Behaviour |
|---|---|
| Overlapping runs | Skipped — if `run_scheduled` is still running when the next trigger fires, the new trigger is ignored |
| Missed executions (server down) | Skipped — no catch-up queue |
| Exception in `run_scheduled` | Logged with `logger.exception()`; future triggers continue normally |
| Plugin deleted / reloaded | All scheduled jobs are removed immediately |

---

## Complete Minimal Plugin

A copy-paste-ready starting point:

```python
# my_package/plugin.py
from __future__ import annotations

import logging
from typing import Any, Optional

from app.models.db.document_with_score import DocumentWithScore
from app.services.puaia.plugin.puaia_plugin import PuAiAPlugin
from app.services.puaia.vector_store.vector_store_retrieval_config import (
    VectorStoreRetrievalConfig,
)

logger = logging.getLogger(__name__)


class MyPlugin(PuAiAPlugin):

    def __init__(self, config: Optional[dict[str, Any]] = None):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")

    def get_name(self) -> str:
        return "my_plugin"

    def get_vector_store_retrieval_config(self) -> VectorStoreRetrievalConfig:
        return VectorStoreRetrievalConfig(
            similarity_score_limit=0.90,
            hybrid_search_alpha=0.5,
        )

    def doc_to_text(self, doc: DocumentWithScore) -> str:
        return doc.document.page_content + "\n---"

    def get_system_prompt(self) -> str:
        return (
            "You are a helpful assistant. "
            "Answer questions using only the provided context. "
            "If unsure, say so."
        )
```

---

## Plugin Lifecycle & API Workflow

### 1. Register

```http
POST /plugins
Content-Type: application/json

{"gitUrl": "https://github.com/your-org/my-puaia-plugin"}
```

Returns **202** immediately. Installation runs as a background task:
```json
{
  "id": "uuid",
  "name": "__pending__",
  "gitUrl": "https://github.com/your-org/my-puaia-plugin",
  "status": "PENDING",
  "createdAt": "2026-05-10T12:00:00Z"
}
```

### 2. Poll installation status

```http
GET /plugins/{name}
```

The name becomes the return value of `get_name()` once the class is loaded.
Poll until `status` is one of:

| Status | Meaning |
|---|---|
| `PENDING` | Queued, not yet started |
| `INSTALLING` | git clone + uv install + class load in progress |
| `PENDING_CONFIG` | Installed, but required config fields are missing |
| `ACTIVE` | Fully loaded and registered — ready to use |
| `FAILED` | Installation failed; check `error` field for the traceback |

### 3. Provide configuration (if `PENDING_CONFIG`)

```http
GET /plugins/{name}/config
```

Returns the schema from `config.toml` and current (masked) values:

```json
{
  "schema_fields": [
    {"name": "api_key", "type": "string", "required": true, "description": "..."},
    {"name": "max_results", "type": "integer", "required": false, "default": 10, "description": "..."}
  ],
  "values": {
    "api_key": "***"
  }
}
```

Set values with:

```http
PATCH /plugins/{name}/config
Content-Type: application/json

{"values": {"api_key": "sk-abc123", "max_results": 20}}
```

If all required fields are now present, the plugin auto-transitions from `PENDING_CONFIG` to `ACTIVE`.

### 4. Use the plugin

Once `ACTIVE`, the plugin's `name` is available as the `plugin` parameter in all PuAiA RAG endpoints (`/ask`, `/store`, `/scores`).

### 5. Hot-reload (after updating the Git repo)

```http
POST /plugins/{name}/reload
```

Returns **202**. Re-pulls the repository, reinstalls dependencies, reloads the class. The plugin goes through `INSTALLING` again and returns to `ACTIVE` (or `PENDING_CONFIG` / `FAILED`).

### 6. Remove

```http
DELETE /plugins/{name}
```

Returns **204**. Unregisters the plugin from the in-memory registry and removes the DB record. The cloned directory on disk is **not** deleted automatically.

---

## Status State Machine

```
POST /plugins
      │
      ▼
   PENDING
      │
      ▼ (background task starts)
  INSTALLING
      │
      ├─── no config.toml required fields ──► ACTIVE
      │
      ├─── required config fields missing ──► PENDING_CONFIG
      │                                             │
      │                                   PATCH /config (all satisfied)
      │                                             │
      │                                             ▼
      │                                           ACTIVE
      │
      └─── any error ──────────────────────► FAILED
                                               (check `error` field)
```

---

## Python Code Style Rules

Follow the project's conventions when writing plugin code.

### Imports — three groups separated by blank lines

```python
# 1. Standard library
import logging
import re
from datetime import datetime
from typing import Any, Optional

# 2. Third-party
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 3. Local (PuAiA)
from app.models.db.document_with_score import DocumentWithScore
from app.services.puaia.plugin.puaia_plugin import PuAiAPlugin
from app.services.puaia.vector_store.vector_store_retrieval_config import VectorStoreRetrievalConfig
```

Add `from __future__ import annotations` before group 1 when forward references are needed.

### Naming

| Entity | Convention | Example |
|---|---|---|
| Class | PascalCase | `MyDataSourcePlugin` |
| Methods / variables | snake_case | `get_name`, `api_key` |
| Constants | UPPER_CASE | `DEFAULT_SCORE_LIMIT` |
| Private methods | leading `_` | `_build_context` |

### Type hints

Use `Optional[X]` (not `X | None`) — explicit project convention:

```python
# correct
def get_config_value(self, key: str) -> Optional[str]:
    return self.config.get(key)

# wrong — do not use
def get_config_value(self, key: str) -> str | None:
    ...
```

### Docstrings — Google style

```python
def doc_to_text(self, doc: DocumentWithScore) -> str:
    """Convert a retrieved document into LLM-ready text.

    Args:
        doc: The retrieved document with similarity score.

    Returns:
        Formatted string to be appended to the LLM context.
    """
```

### `__init__` pattern

```python
def __init__(self, config: Optional[dict[str, Any]] = None):
    super().__init__(config)   # always call super — sets self.config
    # read your config values here, with safe defaults
    self.api_key = self.config.get("api_key", "")
```

---

## Testing Your Plugin Locally

### 1. Install the plugin package in development mode

```bash
# inside your plugin repository
uv pip install -e .
```

### 2. Write a unit test

Plugins can be tested without a running PuAiA server. Instantiate the class directly:

```python
# tests/test_my_plugin.py
from unittest.mock import MagicMock
from langchain_core.documents import Document

from my_package.plugin import MyPlugin
from app.models.db.document_with_score import DocumentWithScore


def test_get_name():
    plugin = MyPlugin()
    assert plugin.get_name() == "my_plugin"


def test_doc_to_text():
    plugin = MyPlugin(config={"api_key": "test-key"})
    doc = DocumentWithScore(
        document=Document(page_content="Hello world", metadata={"source": "test"}),
        score=0.95,
    )
    result = plugin.doc_to_text(doc)
    assert "Hello world" in result


def test_config_injection():
    plugin = MyPlugin(config={"api_key": "sk-abc", "max_results": 5})
    assert plugin.api_key == "sk-abc"


def test_retrieval_config():
    plugin = MyPlugin()
    config = plugin.get_vector_store_retrieval_config()
    assert config.similarity_score_limit <= 1.0
    assert config.documents_limit > 0
```

Run with:
```bash
uv run pytest tests/ -v
```

### 3. Verify the entry point resolves

After `uv pip install -e .`:

```python
import importlib.metadata
eps = importlib.metadata.entry_points(group="puaia.plugins")
for ep in eps:
    print(ep.name, ep.value, ep.load())
```

---

## Common Mistakes

| Mistake | Fix |
|---|---|
| `get_name()` returns different values at different times | Return a constant string — it is used as a DB key |
| Not calling `super().__init__(config)` | `self.config` will be undefined; always call super |
| Required config field set but plugin stays `PENDING_CONFIG` | Check the field `name` in config.toml exactly matches the key in `PATCH /config` values |
| Plugin status is `FAILED` | Call `GET /plugins/{name}` and read the `error` field for the full traceback |
| `[tool.puaia] plugin` dotted path wrong | Must be `"package.module.ClassName"` — the last segment is the class, everything before is the import path |
| Plugin not found after `uv pip install` | Run `importlib.invalidate_caches()` or restart the Python process; the loader does this automatically |
| Storing a mutable default in `__init__` | Use `self.config.get("key") or default` — config values are plain Python types |
