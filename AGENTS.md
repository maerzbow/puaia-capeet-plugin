# Capeet PuAiA Plugin — Agent Instructions

## Entry points

```
pyproject.toml:    [tool.puaia] plugin = "capeet.plugin.CapeetPlugin"
entry-points:      capeet = "capeet.plugin:CapeetPlugin"
```

## Commands

```bash
uv pip install -e .          # install plugin in dev mode (required before registration)
uv run pytest tests/ -v      # run tests (no tests dir exists yet)
```

## Repo layout

```
capeet/plugin.py     — PuAiAPlugin ABC subclass with all logic
capeet/__init__.py   — empty
config.toml          — declares required `firecrawl_api_key` field
pyproject.toml       — package metadata + ruff + PuAiA discovery config
CONTEXT.md           — domain glossary (source of truth for terms)
.agents/skills/puaia-plugin/SKILL.md — full PuAiA plugin reference
```

## Architecture

- **Scrapes** `capeet.com/gigs_list.html` daily at 06:00 via Firecrawl API.
- **Parses** Firecrawl markdown output with regex (`capeet/plugin.py:28-40`).
- **Stores** one `StoreRequest` per gig (no dedup — every run stores all).
- **Metadata per gig:** `date`, `venue`, `city`, `bands` (list), `facebook_url` (optional), `status` (optional: `"cancelled"` / `"sold_out"`).
- **Year inference** (`_infer_year`): if parsed month < current month → next calendar year (covers May–May window).
- **Retrieval config:** similarity `0.85`, limit `50`, hybrid alpha `0.5`.

## Code conventions

- `from __future__ import annotations` at top.
- Imports: 3 groups (stdlib, third-party, PuAiA local), separated by blank lines.
- `Optional[X]` not `X | None`.
- Google-style docstrings.
- Constants `UPPER_CASE`, private methods `_prefixed`.
- Hatchling build backend.

## Gotchas

- The PuAiA plugin authoring guide lives at `.agents/skills/puaia-plugin/SKILL.md`.
