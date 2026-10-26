#!/usr/bin/env python3
"""
Runs the full weekly pipeline:
  1. Scrape chamber websites (scraper.py)
  2. Fetch and parse chamber newsletters from Gmail (gmail_fetch.py + newsletter_parser.py)
  3. Merge, dedupe, sort, and build clean display fields
  4. Write the pages GitHub Pages serves:
       docs/summary.html  - compact preview (next 3 events) for the eurocham.org homepage
       docs/index.html    - full list of upcoming events
       docs/events.json   - clean data (no scraped page debris)

Run manually with:  python generate_page.py
Run automatically via .github/workflows/weekly.yml
"""

import subprocess
import sys
import json
import csv
import re
import html as htmllib
from datetime import datetime, timezone, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from newsletter_parser import extract_events_from_text

try:
    from gmail_fetch import fetch_newsletters
    GMAIL_AVAILABLE = True
except Exception as e:
    print(f"Gmail fetch unavailable this run: {e}", file=sys.stderr)
    GMAIL_AVAILABLE = False


# ---------------------------------------------------------------- settings
# Where the "View all events" link on the summary points. Once the eurocham.org
# events page exists, replace this with its address.
FULL_PAGE_URL = "https://davidhubert.github.io/eurocham-events/"

# How many events the summary renders (the embed shows 3 by default; the
# address can ask for fewer or more, up to this number, with ?n=5).
SUMMARY_MAX = 8
SUMMARY_DEFAULT = 3

# Short names shown on the pages. First matching fragment wins.
CHAMBER_SHORT_NAMES = [
    ("american-slovenian", "ASC"),
    ("belgian-american", "BelCham"),
    ("belwest", "Belwest"),
    ("belgian & luxembourg", "Belwest"),
    ("british american", "BABC"),
    ("babcsf", "BABC"),
    ("business association italy", "BAIA"),
    ("baia", "BAIA"),
    ("california spain", "California Spain Chamber"),
    ("czechinvest", "CzechInvest"),
    ("danish american", "DACC"),
    ("daccncal", "DACC"),
    ("french american", "FACC"),
    ("facc", "FACC"),
    ("german american business association", "GABA"),
    ("gaba", "GABA"),
    ("gacc", "GACC West"),
    ("hungarian", "HACC"),
    ("amhuchamber", "HACC"),
    ("irish network", "INBA"),
    ("irishnetwork", "INBA"),
    ("open austria", "Open Austria"),
    ("advantageaustria", "Open Austria"),
    ("portugal", "AICEP"),
    ("romanian", "RACC"),
    ("racc", "RACC"),
    ("slovak", "Slovak Pro"),
    ("swedish", "SACC-SF/SV"),
    ("sacc-sf", "SACC-SF/SV"),
    ("swiss", "Swiss-American Chamber"),
    ("saccsf", "Swiss-American Chamber"),
    ("us-polish", "USPTC"),
    ("usptc", "USPTC"),
]


def short_chamber(name: str) -> str:
    """Display name for a chamber; handles 'A / B' when two chambers announced the same event."""
    if not name:
        return "Chamber"
    parts = [p.strip() for p in name.split(" / ")]
    out = []
    for p in parts:
        low = p.lower()
        label = next((short for frag, short in CHAMBER_SHORT_NAMES if frag in low), p)
        if label not in out:
            out.append(label)
    return " / ".join(out)


# --------------------------------------------------------------- pipeline
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
            # keep a link if only one of the two copies has one
            if not seen[key].get("url") and e.get("url"):
                seen[key]["url"] = e["url"]
        else:
            seen[key] = dict(e)
    return list(seen.values())


def filter_future(events: list[dict]) -> list[dict]:
    """Keep only events dated today or later. Events with no parseable
    date are dropped, since we can't confirm they're upcoming."""
    today = datetime.now(timezone.utc).date().isoformat()
    return [e for e in events if e.get("date_parsed") and e["date_parsed"] >= today]


# ------------------------------------------------------ display-text cleaning
# The scraper keeps a blob of text found near each event ("location_raw") so it
# can decide whether the event is in the Bay Area. That blob is for sorting
# events, not for showing to people: it can contain menu items, buttons, HTML
# and the title repeated. Everything below turns it into a short, safe excerpt,
# or into nothing at all when it can't be trusted.

JUNK_WORDS = re.compile(
    r"log ?in|sign ?in|sign ?up|skip to|menu|cookie|privacy|javascript|"
    r"©|copyright|subscribe|newsletter|add to calendar|share this|"
    r"read more|learn more|click here|register now|my account|members? area|"
    r"search|facebook|linkedin|twitter|instagram|download",
    re.IGNORECASE,
)

DATE_BITS = re.compile(
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|"
    r"\b\d{4}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}\b|"
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?,?(?:\s+\d{4})?\b",
    re.IGNORECASE,
)


def clean_text(raw: str) -> str:
    """Strip HTML tags and entities, bullets and extra whitespace."""
    if not raw:
        return ""
    t = re.sub(r"<[^>]+>", " ", raw)
    t = htmllib.unescape(t)
    t = t.replace("\xa0", " ").replace("•", " · ")
    t = re.sub(r"\s+", " ", t).strip(" ·|-–—,;:")
    return t


def build_excerpt(e: dict, max_len: int = 240) -> str:
    """Short readable description, or '' when nothing trustworthy is available."""
    raw = clean_text(e.get("location_raw", ""))
    if not raw:
        return ""
    title = clean_text(e.get("title", ""))

    # remove the title (scraped blobs often repeat it) and any dates
    if title:
        raw = re.sub(re.escape(title), " ", raw, flags=re.IGNORECASE)
    raw = DATE_BITS.sub(" ", raw)
    raw = re.sub(r"^\s*Event\b", " ", raw)
    raw = re.sub(r"^\s*San Francisco area\b", " ", raw)  # FACC region label
    raw = raw.replace("…", " ")

    # keep sentence-like segments; drop menu-ish or too-short fragments
    segments = [s.strip(" ·-–—,;:") for s in re.split(r"\s*\|\s*", raw)]
    good = [s for s in segments if len(s) >= 15 and not JUNK_WORDS.search(s)]
    if not good:
        return ""
    text = " ".join(good)
    text = re.sub(r"\s+", " ", text).strip()

    # a real description reads like sentences; menus read like a pile of short words
    words = text.split()
    if len(words) < 4:
        return ""
    if sum(1 for w in words if w[:1].isupper()) / len(words) > 0.75 and len(words) > 8:
        return ""  # almost every word capitalised: likely a nav/menu dump

    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0].rstrip(" ,;:.-–—") + "…"
    return text


def safe_url(url: str) -> str:
    url = (url or "").strip()
    return url if url.lower().startswith(("http://", "https://")) else ""


def pretty_date(iso: str) -> str:
    try:
        d = date.fromisoformat(iso)
        return f"{d.strftime('%a')}, {d.strftime('%b')} {d.day}, {d.year}"
    except Exception:
        return iso or "Date TBA"


def prepare(events: list[dict]) -> list[dict]:
    """Clean display records, future events only, soonest first."""
    out = []
    for e in sorted(filter_future(events), key=lambda e: e["date_parsed"]):
        out.append({
            "date": e["date_parsed"],
            "date_display": pretty_date(e["date_parsed"]),
            "title": clean_text(e.get("title", "")) or "Untitled event",
            "excerpt": build_excerpt(e),
            "chamber": short_chamber(e.get("chamber", "")),
            "url": safe_url(e.get("url", "")),
            "source": e.get("source_detail", ""),
        })
    return out


# ------------------------------------------------------------------ HTML
esc = lambda s: htmllib.escape(str(s or ""), quote=True)

# Shared look. Transparent background so it blends into the eurocham.org page.
BASE_CSS = """
  :root { --ink:#1a1a1a; --muted:#5f6368; --accent:#1a5fb4; --line:#e3e5e8; --chip:#eef2ff; --chipink:#3730a3; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 0; color: var(--ink); background: transparent; line-height: 1.35; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .chip { display: inline-block; background: var(--chip); color: var(--chipink);
          border-radius: 4px; padding: 1px 7px; font-size: 0.75rem; font-weight: 600; white-space: nowrap; }
"""

SUMMARY_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EuroCham SF — Upcoming Events (summary)</title>
<style>{base_css}
  body {{ padding: 2px; }}
  ul {{ list-style: none; margin: 0; padding: 0; }}
  li {{ background: #fff; border: 1px solid var(--line); border-radius: 8px;
        padding: 12px 16px; margin-bottom: 10px; }}
  .date {{ font-size: 0.8rem; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: .02em; }}
  .title {{ display: block; font-size: 1rem; font-weight: 600; color: var(--ink); margin: 2px 0 3px; }}
  .excerpt {{ font-size: 0.85rem; color: var(--muted); margin: 0 0 6px;
              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .more {{ padding: 2px 4px 6px; font-size: 0.9rem; font-weight: 600; }}
  .empty {{ color: var(--muted); font-style: italic; padding: 10px 0; }}
</style>
</head>
<body>
{items_html}
<script>
  /* ?n=5 shows five events, ?excerpt=0 hides the one-line description */
  (function () {{
    try {{
      var q = new URLSearchParams(location.search);
      var n = parseInt(q.get('n'), 10) || {default_n};
      var items = document.querySelectorAll('li[data-i]');
      items.forEach(function (li) {{ li.style.display = (+li.dataset.i >= n) ? 'none' : ''; }});
      if (q.get('excerpt') === '0') {{
        document.querySelectorAll('.excerpt').forEach(function (p) {{ p.style.display = 'none'; }});
      }}
    }} catch (e) {{}}
  }})();
</script>
</body>
</html>
"""

SUMMARY_ITEM = """<li data-i="{i}"{hidden}>
  <div class="date">{date_display}</div>
  {title_html}
  {excerpt_html}
  <span class="chip">{chamber}</span>
</li>"""

FULL_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EuroCham SF — Bay Area Chamber Events</title>
<style>{base_css}
  body {{ max-width: 820px; margin: 0 auto; padding: 8px 12px 24px; }}
  .updated {{ color: var(--muted); font-size: 0.8rem; margin: 0 0 16px; }}
  .event {{ background: #fff; border: 1px solid var(--line); border-radius: 8px;
            padding: 14px 18px; margin-bottom: 10px; }}
  .event-date {{ font-weight: 700; color: var(--accent); font-size: 0.85rem; }}
  .event-title {{ font-size: 1.08rem; font-weight: 600; margin: 3px 0 4px; }}
  .event-title a {{ color: var(--ink); }}
  .event-excerpt {{ color: var(--muted); font-size: 0.9rem; margin: 0 0 6px; }}
  .event-foot {{ font-size: 0.8rem; color: var(--muted); }}
  .event-foot a {{ font-weight: 600; margin-left: 8px; }}
  .empty {{ color: var(--muted); font-style: italic; }}
</style>
</head>
<body>
<p class="updated">{count} upcoming Bay Area events from our member chambers · updated {updated}</p>
{events_html}
</body>
</html>
"""

FULL_EVENT = """<div class="event">
  <div class="event-date">{date_display}</div>
  <div class="event-title">{title_html}</div>
  {excerpt_html}
  <div class="event-foot"><span class="chip">{chamber}</span>{link_html}</div>
</div>"""


def render_summary(items: list[dict]) -> str:
    if not items:
        body = '<p class="empty">No upcoming events listed right now.</p>'
    else:
        lis = []
        for i, e in enumerate(items[:SUMMARY_MAX]):
            if e["url"]:
                title_html = (f'<a class="title" href="{esc(e["url"])}" target="_blank" '
                              f'rel="noopener">{esc(e["title"])}</a>')
            else:
                title_html = f'<span class="title">{esc(e["title"])}</span>'
            excerpt_html = (f'<p class="excerpt">{esc(e["excerpt"])}</p>'
                            if e["excerpt"] else "")
            lis.append(SUMMARY_ITEM.format(
                i=i,
                hidden=' style="display:none"' if i >= SUMMARY_DEFAULT else "",
                date_display=esc(e["date_display"]),
                title_html=title_html,
                excerpt_html=excerpt_html,
                chamber=esc(e["chamber"]),
            ))
        body = "<ul>\n" + "\n".join(lis) + "\n</ul>"
    body += (f'\n<div class="more"><a href="{esc(FULL_PAGE_URL)}" target="_top">'
             f'View all events →</a></div>')
    return SUMMARY_PAGE.format(base_css=BASE_CSS, items_html=body, default_n=SUMMARY_DEFAULT)


def render_full(items: list[dict]) -> str:
    if not items:
        events_html = '<p class="empty">No upcoming Bay Area events found this week.</p>'
    else:
        blocks = []
        for e in items:
            if e["url"]:
                title_html = f'<a href="{esc(e["url"])}" target="_blank" rel="noopener">{esc(e["title"])}</a>'
                link_html = f'<a href="{esc(e["url"])}" target="_blank" rel="noopener">Details →</a>'
            else:
                title_html = esc(e["title"])
                link_html = ""
            excerpt_html = f'<p class="event-excerpt">{esc(e["excerpt"])}</p>' if e["excerpt"] else ""
            blocks.append(FULL_EVENT.format(
                date_display=esc(e["date_display"]), title_html=title_html,
                excerpt_html=excerpt_html, chamber=esc(e["chamber"]), link_html=link_html,
            ))
        events_html = "\n".join(blocks)
    return FULL_PAGE.format(
        base_css=BASE_CSS,
        count=len(items),
        updated=datetime.now(timezone.utc).strftime("%b %d, %Y"),
        events_html=events_html,
    )


def main():
    website_events = run_website_scraper()
    newsletter_events = run_newsletter_pipeline()
    merged = dedupe(website_events + newsletter_events)
    items = prepare(merged)

    docs_dir = Path(__file__).parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "index.html").write_text(render_full(items), encoding="utf-8")
    (docs_dir / "summary.html").write_text(render_summary(items), encoding="utf-8")
    (docs_dir / "events.json").write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {len(items)} upcoming events to docs/index.html and docs/summary.html")


if __name__ == "__main__":
    main()
