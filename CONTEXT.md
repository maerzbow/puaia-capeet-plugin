# Capeet PuAiA Plugin — Glossary

## Domain

- **Capeet** — An Austrian concert listing website (capeet.com). Lists upcoming and past gigs in Austria (and occasionally neighboring countries). Gigs are organized by calendar week. The site also maintains yearly archive pages.
- **Gig** — A single concert event entry on capeet.com. Contains: date, one or more band names with optional country of origin and links, venue name, city, optional Facebook event link, and optional status tags (e.g. "sold out", "cancelled").
- **Firecrawl** — A web scraping / crawling API service used by the plugin to retrieve the capeet.com gig listings page as clean Markdown. The plugin requires a Firecrawl API key.

## Plugin

- **Plugin name:** `capeet`
- **Cron schedule:** `0 6 * * *` (daily at 06:00)
- **Ingestion granularity:** one document per gig
- **Ingestion scope:** [`gigs_list.html`](https://www.capeet.com/gigs_list.html) only (main list)
- **Parsing:** regex-based extraction from Firecrawl markdown output
- **Deduplication:** store all gigs on every run (no dedup logic)
- **Metadata per gig:** date, bands (list), venue, city, facebook_url (optional), status (optional, e.g. "sold_out", "cancelled")
- **Cancelled gigs:** stored alongside active gigs with `status: "cancelled"`
- **Config:** Firecrawl API key only
