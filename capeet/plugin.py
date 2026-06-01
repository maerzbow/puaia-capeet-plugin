from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Optional

import httpx

from app.models.db.document_with_score import DocumentWithScore
from app.models.puaia import StoreRequest
from app.services.puaia.plugin.puaia_plugin import PuAiAPlugin
from app.services.puaia.vector_store.vector_store_retrieval_config import (
    VectorStoreRetrievalConfig,
)

if False:
    from app.services.plugin_manager.scheduled_task_context import (
        ScheduledTaskContext,
    )
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"
CAPEET_URL = "https://www.capeet.com/gigs_list.html"

_GIG_LINE_RE = re.compile(
    r"^\s*(\d{2})\.(\d{2})\.\s*:\s*\*\*(.+?)\*\*\s*@\s*_(.+?)_\s*(.*?)$"
)

_BAND_LINK_RE = re.compile(
    r"\[([^\]]+)\]\(([^)]+)\)(?:\s*\(([^)]*)\))?"
)

_BAND_SPLIT_RE = re.compile(r"\s+/\s+")

_MD_LINK_RE = re.compile(r"\[\\\[(\w+)\\\]\]\(([^)]+)\)")

_TAG_RE = re.compile(r"\\\[([^\\\]]+)\\\]")


class CapeetPlugin(PuAiAPlugin):

    def __init__(self, config: Optional[dict[str, Any]] = None):
        super().__init__(config)
        self.firecrawl_api_key = self.config.get("firecrawl_api_key", "")

    def get_name(self) -> str:
        return "capeet"

    def get_cron_schedule(self) -> list[str]:
        return ["0 6 * * *"]

    def get_vector_store_retrieval_config(self) -> VectorStoreRetrievalConfig:
        return VectorStoreRetrievalConfig(
            similarity_score_limit=0.85,
            documents_limit=50,
            hybrid_search_alpha=0.5,
        )

    def doc_to_text(self, doc: DocumentWithScore) -> str:
        """Convert a retrieved document into LLM-ready text."""
        return doc.document.page_content + "\n---"

    def get_system_prompt(self) -> str:
        return (
            "You are an assistant specialising in Austrian concert listings. "
            "Answer questions about upcoming and past gigs, bands, venues, "
            "and cities using only the provided context. If unsure, say so."
        )

    # ------------------------------------------------------------------
    # Gig parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_year(month: int) -> int:
        """Infer the year for a gig date.

        Capeet's main page covers the window from the current May through
        the following May.  Entries with month < current month belong to
        the next calendar year.
        """
        today = datetime.now()
        year = today.year
        if month < today.month:
            year += 1
        return year

    @staticmethod
    def _parse_bands(bold_content: str) -> list[dict[str, str]]:
        """Extract bands from the bold section of a gig line.

        Returns a list of dicts each with 'name', 'link' and 'country'.
        """
        segments = _BAND_SPLIT_RE.split(bold_content.strip())
        bands = []

        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue

            link_match = _BAND_LINK_RE.match(seg)
            if link_match:
                bands.append({
                    "name": link_match.group(1).strip(),
                    "link": link_match.group(2).strip(),
                    "country": (link_match.group(3) or "").strip(),
                })
            else:
                country = ""
                name = seg
                country_match = re.search(r"\(([^)]+)\)\s*$", seg)
                if country_match:
                    country = country_match.group(1).strip()
                    name = seg[: country_match.start()].strip()
                bands.append({"name": name, "link": "", "country": country})

        return bands

    @staticmethod
    def _parse_venue_city(italic_content: str) -> tuple[str, str]:
        """Extract venue and city from the italic section.

        Returns (venue, city).  City falls back to an empty string when
        no comma separator is found.
        """
        parts = italic_content.rsplit(",", 1)
        venue = parts[0].strip()
        city = parts[1].strip() if len(parts) > 1 else ""
        return venue, city

    @staticmethod
    def _parse_rest(rest: str) -> tuple[Optional[str], Optional[str]]:
        """Parse the trailing portion of a gig line.

        Returns (facebook_url, status) where status is one of
        ``"cancelled"``, ``"sold_out"`` or ``None``.
        """
        status = None
        facebook_url = None

        cleaned = ""
        cursor = 0
        for m in _MD_LINK_RE.finditer(rest):
            link_text = m.group(1).lower()
            link_url = m.group(2)
            if link_text == "fb":
                facebook_url = link_url
            cleaned += rest[cursor : m.start()]
            cursor = m.end()
        cleaned += rest[cursor:]

        for tag_match in _TAG_RE.finditer(cleaned):
            tag = tag_match.group(1).strip().lower()
            if re.match(r"^cancelled[.!]?$", tag):
                status = "cancelled"
            elif "sold out" in tag:
                status = "sold_out"

        return facebook_url, status

    def _parse_gigs(self, markdown: str) -> list[dict[str, Any]]:
        """Parse Firecrawl markdown output into individual gig entries."""
        raw_lines = re.split(r"<br>\s*|\n", markdown)
        gigs = []

        for line in raw_lines:
            line = line.strip()
            if not line:
                continue

            match = _GIG_LINE_RE.match(line)
            if not match:
                continue

            day = int(match.group(1))
            month = int(match.group(2))
            bold_content = match.group(3)
            italic_content = match.group(4)
            rest = match.group(5)

            gig_date = date(self._infer_year(month), month, day)
            venue, city = self._parse_venue_city(italic_content)
            bands = self._parse_bands(bold_content)
            facebook_url, status = self._parse_rest(rest)

            gigs.append({
                "date": gig_date.isoformat(),
                "bands": [b["name"] for b in bands],
                "venue": venue,
                "city": city,
                "facebook_url": facebook_url,
                "status": status,
            })

        return gigs

    @staticmethod
    def _gig_to_text(gig: dict[str, Any]) -> str:
        """Format a parsed gig as natural prose for the vector store."""
        bands_text = ", ".join(gig["bands"])
        date_obj = date.fromisoformat(gig["date"])
        date_formatted = date_obj.strftime("%B %d, %Y")
        parts = [f"{bands_text} at {gig['venue']}, {gig['city']} on {date_formatted}"]
        if gig.get("status"):
            parts.append(f"status: {gig['status'].replace('_', ' ')}")
        return ". ".join(parts) + "."

    # ------------------------------------------------------------------
    # Scheduled task
    # ------------------------------------------------------------------

    async def run_scheduled(
        self, db_engine: AsyncEngine, ctx: ScheduledTaskContext
    ) -> None:
        """Scrape capeet.com daily and store each gig in the vector store."""
        if not self.firecrawl_api_key:
            logger.warning("No Firecrawl API key configured, skipping scrape")
            return

        logger.info("Scraping capeet.com via Firecrawl")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                FIRECRAWL_SCRAPE_URL,
                headers={
                    "Authorization": f"Bearer {self.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json={"url": CAPEET_URL},
            )
            resp.raise_for_status()
            result = resp.json()

        markdown = result.get("data", {}).get("markdown", "")
        if not markdown:
            logger.warning("No markdown content returned from Firecrawl")
            return

        gigs = self._parse_gigs(markdown)
        logger.info("Parsed %d gigs from capeet.com", len(gigs))

        for gig in gigs:
            metadata: dict[str, Any] = {
                "date": gig["date"],
                "venue": gig["venue"],
                "city": gig["city"],
            }
            if gig.get("bands"):
                metadata["bands"] = gig["bands"]
            if gig.get("facebook_url"):
                metadata["facebook_url"] = gig["facebook_url"]
            if gig.get("status"):
                metadata["status"] = gig["status"]

            await ctx.store(
                plugin_name=self.get_name(),
                request=StoreRequest(
                    text=self._gig_to_text(gig),
                    metadata=metadata,
                ),
            )

        logger.info("Stored %d gigs", len(gigs))
