#!/usr/bin/env python3
"""
Runs the full weekly pipeline:
  1. Scrape chamber websites (scraper.py)
  2. Fetch and parse chamber newsletters from Gmail (gmail_fetch.py + newsletter_parser.py)
  3. Merge, dedupe, sort
  4. Write docs/index.html — the page GitHub Pages serves

Run manually with:  python generate_page.py
Run automatically via .github/workflows/weekly.yml
"""

import subprocess
import sys
import json
import csv
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from newsletter_parser import extract_events_from_text

try:
    from gmail_fetch import fetch_newsletters
    GMAIL_AVAILABLE = True
except Exception as e:
    print(f"Gmail fetch unavailable this run: {e}", file=sys.stderr)
    GMAIL_AVAILABLE = False


def run_website_scraper():
    """Runs scraper.py as a subprocess (it writes output/events.csv)."""
    subprocess.run([sys.executable, "scraper.py"], check=True, cwd=Path(__file__).parent)
    events = []
    csv_path = Path(__file__).parent / "output" / "events.csv"
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["source_detail"] = "Website"
                events.append(row)
    return events


def run_newsletter_pipeline():
    if not GMAIL_AVAILABLE:
        return []
    events = []
    for msg in fetch_newsletters():
        chamber = msg["sender"].split("@")[-1].split(">")[0].split(".")[0]
        found = extract_events_from_text(
            chamber=chamber,
            source_subject=msg["subject"],
            source_date=msg["date"],
            text=msg["plaintextBody"],
        )
        for e in found:
            if e.region in ("bay_area", "virtual"):
                events.append({
                    "chamber": e.chamber, "title": e.title, "date_raw": e.date_raw,
                    "date_parsed": e.date_parsed, "location_raw": e.location_raw,
                    "region": e.region, "url": "", "source_detail": "Newsletter",
                })
    return events


def dedupe(events: list[dict]) -> list[dict]:
    """Two events count as the same if they share a date and enough of
    their title in common — catches joint events announced by multiple
    chambers (e.g. a shared 'Fall European Networking Evening')."""
    seen = {}
    for e in events:
        key = (e.get("date_parsed"), (e.get("title", "")[:25]).lower())
        if key in seen:
            existing_source = seen[key]["source_detail"]
            if e["source_detail"] not in existing_source:
                seen[key]["source_detail"] = f"{existing_source} + {e['source_detail']}"
            existing_chamber = seen[key]["chamber"]
            if e.get("chamber") and e["chamber"] not in existing_chamber:
                seen[key]["chamber"] = f"{existing_chamber} / {e['chamber']}"
        else:
            seen[key] = dict(e)
    return list(seen.values())


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EuroCham SF — Bay Area Chamber Events</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 800px; margin: 0 auto; padding: 24px 16px; background: #fafafa; color: #1a1a1a; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .updated {{ color: #666; font-size: 0.85rem; margin-bottom: 24px; }}
  .event {{ background: white; border-radius: 8px; padding: 16px 20px; margin-bottom: 12px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .event-date {{ font-weight: 600; color: #1a5fb4; font-size: 0.9rem; }}
  .event-title {{ font-size: 1.05rem; font-weight: 600; margin: 4px 0; }}
  .event-meta {{ color: #555; font-size: 0.88rem; }}
  .event-chamber {{ display: inline-block; background: #eef2ff; color: #3730a3;
                     border-radius: 4px; padding: 2px 8px; font-size: 0.78rem; margin-top: 6px; }}
  .event-source {{ color: #999; font-size: 0.75rem; margin-top: 4px; }}
  a {{ color: #1a5fb4; }}
  .empty {{ color: #888; font-style: italic; }}
</style>
</head>
<body>
<h1>EuroCham SF — Bay Area Chamber Events</h1>
<div class="updated">Last updated {updated}</div>
{events_html}
</body>
</html>
"""

EVENT_TEMPLATE = """<div class="event">
  <div class="event-date">{date_display}</div>
  <div class="event-title">{title}</div>
  <div class="event-meta">{location}</div>
  <span class="event-chamber">{chamber}</span>
  <div class="event-source">Source: {source_detail}{link_html}</div>
</div>
"""


def render_html(events: list[dict]) -> str:
    events_sorted = sorted(events, key=lambda e: e.get("date_parsed") or "9999")
    if not events_sorted:
        events_html = '<p class="empty">No upcoming Bay Area events found this week.</p>'
    else:
        blocks = []
        for e in events_sorted:
            date_display = e.get("date_parsed") or e.get("date_raw") or "Date TBA"
            link_html = f' — <a href="{e["url"]}">details</a>' if e.get("url") else ""
            blocks.append(EVENT_TEMPLATE.format(
                date_display=date_display,
                title=e.get("title", "Untitled event"),
                location=e.get("location_raw", "") or "Location TBA",
                chamber=e.get("chamber", "Unknown"),
                source_detail=e.get("source_detail", "Unknown"),
                link_html=link_html,
            ))
        events_html = "\n".join(blocks)

    return HTML_TEMPLATE.format(
        updated=datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC"),
        events_html=events_html,
    )


def main():
    website_events = run_website_scraper()
    newsletter_events = run_newsletter_pipeline()
    merged = dedupe(website_events + newsletter_events)

    docs_dir = Path(__file__).parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "index.html").write_text(render_html(merged), encoding="utf-8")
    (docs_dir / "events.json").write_text(json.dumps(merged, indent=2), encoding="utf-8")

    print(f"Wrote {len(merged)} events to docs/index.html")


if __name__ == "__main__":
    main()
