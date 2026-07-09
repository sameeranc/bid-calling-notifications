"""
Source fetchers for the Bid Calling Notifications scanner.

Each fetch_* function returns a list of plain dicts with this shape:
    {
        "id":        str  - stable unique id (used for de-duplication)
        "title":     str
        "url":       str  - link to the notice
        "source":    str  - short machine name, e.g. "worldbank"
        "source_name": str - human-readable name, e.g. "World Bank Procurement Notices"
        "published": str | None - ISO date string if known
        "snippet":   str  - short description/body text used for keyword matching
    }

Every function must be defensive: if the remote site is down, changes its
markup, or times out, log a warning and return an empty list rather than
raising. One bad source should never break the whole run.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("sources")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BidCallingNotificationsBot/1.0; "
        "+https://github.com/) personal-research-scanner"
    )
}
TIMEOUT = 30


def _make_id(*parts: str) -> str:
    raw = "|".join(p.strip() for p in parts if p)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# World Bank - Procurement Notices API (JSON, no auth, reliable)
# Docs: https://search.worldbank.org/api/v2/procnotices
# --------------------------------------------------------------------------
def fetch_worldbank(country_code: str = "LK", rows: int = 100) -> list[dict]:
    url = "https://search.worldbank.org/api/v2/procnotices"
    params = {"format": "json", "countrycode_exact": country_code, "rows": rows}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("World Bank fetch failed: %s", exc)
        return []

    notices = data.get("procnotices") or []
    out = []
    for n in notices:
        title = n.get("bid_description") or n.get("project_name") or "Untitled notice"
        notice_id = n.get("id") or _make_id(title, n.get("project_id", ""))
        snippet_parts = [
            n.get("project_name", ""),
            n.get("project_ctry_name", ""),
            n.get("notice_type", ""),
            n.get("procurement_method_name", ""),
            re.sub("<[^<]+?>", " ", n.get("notice_text", "") or "")[:600],
        ]
        out.append(
            {
                "id": _make_id("worldbank", str(notice_id)),
                "title": title.strip(),
                "url": f"https://projects.worldbank.org/en/projects-operations/procurement/notice/{notice_id}"
                if notice_id
                else "https://projects.worldbank.org/en/projects-operations/procurement",
                "source": "worldbank",
                "source_name": "World Bank Procurement Notices",
                "published": n.get("noticedate"),
                "snippet": " | ".join(s for s in snippet_parts if s),
            }
        )
    return out


# --------------------------------------------------------------------------
# ReliefWeb Jobs API (JSON, no auth). Covers many UN / NGO / bilateral
# consultancy postings, including Sri Lanka-based and Sri Lanka-focused work.
# Docs: https://apidoc.reliefweb.int/
#
# Uses a structured "country" filter (not a free-text title search) so it
# returns every job ReliefWeb has tagged Sri Lanka - our own keyword.yaml
# matching (done later in main.py) narrows that down by topic.
# --------------------------------------------------------------------------
def fetch_reliefweb(country: str = "Sri Lanka", limit: int = 100) -> list[dict]:
    url = "https://api.reliefweb.int/v1/jobs"
    payload = {
        "appname": "bid-calling-notifications",
        "filter": {"field": "country", "value": country},
        "sort": ["date.created:desc"],
        "limit": limit,
        "fields": {"include": ["title", "date", "url_alias", "body-html", "source"]},
    }
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("ReliefWeb fetch failed: %s", exc)
        return []

    out = []
    for item in data.get("data", []):
        fields = item.get("fields", {})
        title = fields.get("title", "Untitled job")
        link = fields.get("url_alias") or item.get("href") or "https://reliefweb.int/jobs"
        body = re.sub("<[^<]+?>", " ", fields.get("body-html", "") or "")[:600]
        out.append(
            {
                "id": _make_id("reliefweb", str(item.get("id"))),
                "title": title.strip(),
                "url": link,
                "source": "reliefweb",
                "source_name": "ReliefWeb Jobs",
                "published": (fields.get("date") or {}).get("created"),
                "snippet": body,
            }
        )
    return out


# --------------------------------------------------------------------------
# Generic best-effort scraper for Sri Lankan government notice pages.
# These sites have no API and inconsistent HTML, so this pairs each <a> link
# with its surrounding text as context for keyword matching. Best-effort:
# it may miss some notices or grab extra context, but degrades gracefully.
# --------------------------------------------------------------------------
def _scrape_link_blocks(page_url: str, source: str, source_name: str) -> list[dict]:
    try:
        r = requests.get(page_url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("%s fetch failed: %s", source_name, exc)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    seen_hrefs = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        if href in seen_hrefs:
            continue

        link_text = a.get_text(" ", strip=True)
        # Build context: link text + nearest block-level ancestor's text,
        # so generic link labels like "Procurement Notice" still carry the
        # surrounding project title for keyword matching.
        ancestor = a.find_parent(["p", "li", "td", "div"])
        context_text = ancestor.get_text(" ", strip=True) if ancestor else link_text
        context_text = context_text[:500]

        if not link_text and not context_text:
            continue
        # Skip obvious non-notice links (nav, social, mailto, etc.)
        if href.startswith("mailto:") or href.startswith("tel:"):
            continue

        full_url = href if href.startswith("http") else requests.compat.urljoin(page_url, href)
        title = link_text or context_text[:120]

        seen_hrefs.add(href)
        out.append(
            {
                "id": _make_id(source, full_url),
                "title": title,
                "url": full_url,
                "source": source,
                "source_name": source_name,
                "published": _guess_date_from_text(href) or _guess_date_from_text(context_text),
                "snippet": context_text,
            }
        )
    return out


DATE_PATTERNS = [
    re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})"),  # 20260416 / 2026-04-16
]


def _guess_date_from_text(text: str) -> str | None:
    for pat in DATE_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                y, mo, d = m.groups()
                return datetime(int(y), int(mo), int(d)).date().isoformat()
            except ValueError:
                continue
    return None


def fetch_treasury_sl() -> list[dict]:
    return _scrape_link_blocks(
        "https://www.treasury.gov.lk/procurement/procurement-notices",
        "treasury_lk",
        "Sri Lanka Ministry of Finance - Procurement Notices",
    )


def fetch_cbsl() -> list[dict]:
    return _scrape_link_blocks(
        "https://www.cbsl.gov.lk/en/tenders",
        "cbsl",
        "Central Bank of Sri Lanka - Procurement Notices",
    )


# Registry of automated sources. Add new ones here.
ALL_SOURCES = {
    "worldbank": fetch_worldbank,
    "reliefweb": fetch_reliefweb,
    "treasury_lk": fetch_treasury_sl,
    "cbsl": fetch_cbsl,
}
