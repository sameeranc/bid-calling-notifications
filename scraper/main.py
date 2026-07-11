#!/usr/bin/env python3
"""
Bid Calling Notifications - scheduled scan.

Fetches consultancy/tender listings from every source in sources.ALL_SOURCES,
keeps only ones matching config/keywords.yaml, merges them into docs/data.json
(preserving history + "first_seen" dates so the dashboard can flag what's new),
and prunes items older than KEEP_DAYS to keep the file small.

Run manually:
    pip install -r requirements.txt
    python scraper/main.py

Exit code is always 0 unless something structural (e.g. config missing) fails -
individual source failures are logged but do not stop the run.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from sources import ALL_SOURCES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DOCS_DIR = ROOT / "docs"
DATA_FILE = DOCS_DIR / "data.json"
KEEP_DAYS = 120  # prune matched items older than this many days since first_seen

# Acronyms up to this length (spaces ignored) that are written ALL CAPS in
# keywords.yaml are matched case-SENSITIVELY, so e.g. "WHO" only matches the
# literal acronym WHO - not the common word "who" - and "SIA" doesn't
# false-match inside "Asia". Longer/mixed-case entries match as ordinary
# case-insensitive whole-word/phrase matches.
ACRONYM_MAX_LEN = 6


def load_keywords() -> tuple[list[str], list[str]]:
    with open(CONFIG_DIR / "keywords.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    keywords: list[str] = []
    for key, values in cfg.items():
        if key == "location_hints":
            continue
        keywords.extend(values or [])
    location_hints = cfg.get("location_hints") or []
    return keywords, location_hints


def build_matchers(keywords: list[str]) -> list[tuple[re.Pattern, str]]:
    """Compile each keyword into a whole-word/phrase regex.

    Short ALL-CAPS keywords (acronyms) are compiled case-sensitively so they
    only match the literal acronym. Everything else matches case-insensitively.
    """
    matchers = []
    for kw in keywords:
        stripped = kw.strip().strip('"')
        if not stripped:
            continue
        compact = stripped.replace(" ", "")
        is_acronym = compact.isupper() and len(compact) <= ACRONYM_MAX_LEN
        flags = 0 if is_acronym else re.IGNORECASE
        pattern = re.compile(r"\b" + re.escape(stripped) + r"\b", flags)
        matchers.append((pattern, stripped))
    return matchers


def matches(
    item: dict,
    matchers: list[tuple[re.Pattern, str]],
    location_hints: list[str],
    require_location: bool,
) -> list[str]:
    haystack = f"{item.get('title', '')} {item.get('snippet', '')}"
    hit = [kw for pattern, kw in matchers if pattern.search(haystack)]
    if not hit:
        return []
    if require_location and location_hints:
        haystack_lower = haystack.lower()
        if not any(loc.lower() in haystack_lower for loc in location_hints):
            return []
    return hit


def load_existing() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not parse existing data.json (%s), starting fresh", exc)
    return {"generated_at": None, "last_run_status": {}, "items": []}


def main() -> None:
    keywords, location_hints = load_keywords()
    matchers = build_matchers(keywords)
    log.info("Loaded %d keywords (%d treated as case-sensitive acronyms)",
              len(matchers), sum(1 for p, k in matchers if p.flags & re.IGNORECASE == 0))

    existing = load_existing()
    existing_by_id = {it["id"]: it for it in existing.get("items", [])}

    today = datetime.now(timezone.utc).date().isoformat()
    run_status = {}
    new_count = 0
    seen_ids_this_run = set()

    for source_key, fetch_fn in ALL_SOURCES.items():
        # Every registered source already scopes its query to Sri Lanka
        # (World Bank: countrycode_exact=LK, ReliefWeb: country filter,
        # treasury_lk/cbsl: Sri Lanka-only sites), so no extra location
        # check is needed here - only the topic keywords apply.
        require_location = False
        try:
            raw_items = fetch_fn()
            run_status[source_key] = {"ok": True, "fetched": len(raw_items)}
        except Exception as exc:  # noqa: BLE001
            log.error("Source %s crashed: %s", source_key, exc)
            run_status[source_key] = {"ok": False, "error": str(exc)}
            continue

        matched_here = 0
        for raw in raw_items:
            hit_keywords = matches(raw, matchers, location_hints, require_location)
            if not hit_keywords:
                continue
            matched_here += 1
            seen_ids_this_run.add(raw["id"])

            if raw["id"] in existing_by_id:
                # Keep original first_seen, refresh mutable fields.
                existing_by_id[raw["id"]].update(
                    {
                        "title": raw["title"],
                        "url": raw["url"],
                        "snippet": raw["snippet"],
                        "published": raw.get("published"),
                        "matched_keywords": sorted(set(hit_keywords)),
                        "last_seen": today,
                    }
                )
            else:
                new_count += 1
                existing_by_id[raw["id"]] = {
                    **raw,
                    "matched_keywords": sorted(set(hit_keywords)),
                    "first_seen": today,
                    "last_seen": today,
                }

        run_status[source_key]["matched"] = matched_here
        log.info("%s: fetched=%d matched=%d", source_key, len(raw_items), matched_here)

    # Prune old items that are no longer surfaced by any source and are stale.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).date().isoformat()
    kept_items = [
        it
        for it in existing_by_id.values()
        if it["id"] in seen_ids_this_run or it.get("first_seen", today) >= cutoff
    ]
    kept_items.sort(key=lambda it: it.get("first_seen", ""), reverse=True)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_sl_note": "Times are UTC. Sri Lanka is UTC+5:30.",
        "new_today_count": new_count,
        "total_count": len(kept_items),
        "last_run_status": run_status,
        "items": kept_items,
    }

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %s: %d total items, %d new today", DATA_FILE, len(kept_items), new_count)

    _export_manual_sources()


def _export_manual_sources() -> None:
    """Mirror config/manual_sources.yaml to docs/manual_sources.json so the
    dashboard (static HTML/JS) can render the quick-check links without
    needing a YAML parser in the browser."""
    src = CONFIG_DIR / "manual_sources.yaml"
    if not src.exists():
        return
    with open(src, encoding="utf-8") as f:
        items = yaml.safe_load(f) or []
    (DOCS_DIR / "manual_sources.json").write_text(
        json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
