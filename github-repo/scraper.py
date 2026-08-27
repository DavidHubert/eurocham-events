#!/usr/bin/env python3
"""
EuroCham SF — member chamber events scraper.

Tries, per chamber, in order:
  1. Squarespace JSON endpoint  (?format=json on the events page)
  2. RSS / Atom feed autodiscovery
  3. schema.org Event structured data (JSON-LD) on the events page
  4. Generic HTML heuristic: date-pattern text near a heading/link on the events page

Chambers where nothing is found are reported separately so you know where a
custom rule (or manual entry) is needed — see NOTES.md.

Usage:
    python scraper.py                     # scrape all chambers in chambers.yaml
    python scraper.py --chamber Belwest    # scrape just one (substring match)
    python scraper.py --out events.csv     # change output path

Output:
    output/events.csv   — consolidated event list
    output/events.json  — same, as JSON
    output/report.txt   — per-chamber status (found N events / strategy used / failed)
"""

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; EuroChamEventsBot/1.0; +https://www.eurocham.org)"
}
TIMEOUT = 15

DATE_PATTERN = re.compile(
    r"""(
        \b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(st|nd|rd|th)?,?\s+\d{4}\b
        |
        \b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b
        |
        \b\d{4}-\d{2}-\d{2}\b
        |
        \b\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# ---------- Location classification ----------
# Goal: keep Bay Area events (and virtual/online ones, since those aren't
# geographically exclusive), flag everything else for review instead of
# silently dropping it — a missed "San Jose" spelling shouldn't vanish.

BAY_AREA_TERMS = [
    # generic
    "san francisco", "sf,", " sf ", "bay area", "silicon valley",
    "north bay", "east bay", "south bay", "peninsula, ca", "the peninsula",
    # Alameda County
    "oakland", "berkeley", "fremont", "hayward", "san leandro", "alameda",
    "union city", "livermore", "pleasanton", "dublin, ca", "newark, ca",
    "emeryville", "piedmont, ca",
    # Contra Costa County
    "walnut creek", "concord, ca", "richmond, ca", "antioch, ca",
    "san ramon", "danville", "pittsburg, ca", "martinez, ca",
    "pleasant hill", "lafayette, ca", "orinda", "moraga",
    # Marin County
    "san rafael", "novato", "mill valley", "sausalito", "larkspur",
    "corte madera", "tiburon", "san anselmo", "fairfax, ca", "marin county",
    # Napa County
    "napa", "st. helena", "saint helena, ca", "calistoga", "yountville",
    # San Mateo County
    "san mateo", "redwood city", "menlo park", "foster city", "burlingame",
    "south san francisco", "daly city", "half moon bay", "san bruno",
    "millbrae", "belmont, ca", "san carlos",
    # Santa Clara County
    "san jose", "santa clara", "sunnyvale", "mountain view", "palo alto",
    "cupertino", "milpitas", "los gatos", "saratoga, ca", "campbell, ca",
    "gilroy", "morgan hill",
    # Solano County
    "vallejo", "fairfield, ca", "vacaville", "benicia", "suisun",
    # Sonoma County
    "santa rosa", "petaluma", "sebastopol", "rohnert park", "sonoma county",
    "sonoma, ca", "windsor, ca",
]

VIRTUAL_TERMS = [
    "virtual", "online", "webinar", "zoom", "livestream", "live stream",
]

# Other-US and international hints, used only to make the report readable
# (e.g. "this one's in Los Angeles" rather than just "not Bay Area").
OTHER_US_TERMS = [
    "los angeles", "l.a.", "new york", "chicago", "austin", "seattle",
    "boston", "washington, dc", "washington dc", "miami", "denver",
    "portland", "san diego", "atlanta", "houston", "dallas",
]


def classify_location(*text_fragments: str) -> tuple[str, str]:
    """Return (region_flag, matched_snippet). region_flag is one of:
    bay_area, virtual, other_us, international, unknown."""
    blob = " ".join(f for f in text_fragments if f).lower()
    if not blob.strip():
        return "unknown", ""

    for term in BAY_AREA_TERMS:
        if term in blob:
            return "bay_area", term.strip()

    for term in VIRTUAL_TERMS:
        if term in blob:
            return "virtual", term.strip()

    for term in OTHER_US_TERMS:
        if term in blob:
            return "other_us", term.strip()

    # Crude international hint: a country name or a non-US state pattern
    # we didn't already match as Bay Area/other-US. Kept deliberately
    # narrow — better to fall through to "unknown" and get a manual look
    # than mis-tag something.
    intl_hints = ["germany", "deutschland", "belgium", "france", "spain",
                  "switzerland", "austria", "sweden", "denmark", "poland",
                  "portugal", "romania", "slovakia", "slovenia", "hungary",
                  "czech", "ireland", "italy", "brussels", "berlin", "paris"]
    for term in intl_hints:
        if term in blob:
            return "international", term.strip()

    return "unknown", ""


@dataclass
class Event:
    chamber: str
    title: str
    date_raw: str
    date_parsed: Optional[str]
    url: str
    source_strategy: str
    location_raw: str = ""
    region: str = ""       # filled in after scraping, via classify_location
    region_match: str = ""


def try_parse_date(text: str) -> Optional[str]:
    try:
        dt = dateparser.parse(text, fuzzy=True, default=datetime(2026, 1, 1))
        return dt.date().isoformat()
    except (ValueError, OverflowError):
        return None


def fetch(url: str) -> Optional[requests.Response]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 200:
            return resp
    except requests.RequestException as e:
        print(f"    [fetch error] {url}: {e}", file=sys.stderr)
    return None


# ---------- Strategy 1: Squarespace JSON ----------

def strategy_squarespace(chamber_name: str, events_url: str) -> list[Event]:
    json_url = events_url.rstrip("/") + "?format=json"
    resp = fetch(json_url)
    if not resp:
        return []
    try:
        data = resp.json()
    except ValueError:
        return []

    items = data.get("items") or data.get("upcoming") or []
    events = []
    for item in items:
        title = item.get("title", "").strip()
        start = item.get("startDate")
        url_slug = item.get("fullUrl", "")
        if not title:
            continue
        date_parsed = None
        date_raw = ""
        if start:
            # Squarespace startDate is often epoch millis
            try:
                dt = datetime.fromtimestamp(int(start) / 1000)
                date_parsed = dt.date().isoformat()
                date_raw = dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError, OSError):
                pass

        # Squarespace event location, when set, lives under addressLine1/
        # addressLine2/addressCountry, or sometimes a plain "location" string.
        loc = item.get("location") or {}
        if isinstance(loc, dict):
            location_raw = " ".join(str(v) for v in [
                loc.get("addressLine1", ""), loc.get("addressLine2", ""),
                loc.get("addressCountry", ""),
            ] if v)
        else:
            location_raw = str(loc)
        if not location_raw:
            # fall back to the excerpt, which sometimes mentions the venue/city
            location_raw = item.get("excerpt", "") or ""

        events.append(Event(
            chamber=chamber_name,
            title=title,
            date_raw=date_raw,
            date_parsed=date_parsed,
            url=urljoin(events_url, url_slug) if url_slug else events_url,
            source_strategy="squarespace_json",
            location_raw=location_raw,
        ))
    return events


# ---------- Strategy 2: RSS / Atom ----------

def strategy_rss(chamber_name: str, homepage: str, events_url: str) -> list[Event]:
    import feedparser

    candidates = [
        events_url.rstrip("/") + "/rss",
        events_url.rstrip("/") + "/feed",
        homepage.rstrip("/") + "/rss",
        homepage.rstrip("/") + "/feed",
    ]

    # Also check <link rel="alternate" type="application/rss+xml"> on the events page
    resp = fetch(events_url)
    if resp:
        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.find_all("link", rel="alternate"):
            t = link.get("type", "")
            if "rss" in t or "atom" in t:
                href = link.get("href")
                if href:
                    candidates.insert(0, urljoin(events_url, href))

    for url in candidates:
        resp = fetch(url)
        if not resp:
            continue
        feed = feedparser.parse(resp.content)
        if not feed.entries:
            continue
        events = []
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            if not title:
                continue
            raw_date = entry.get("published", "") or entry.get("updated", "")
            # RSS has no dedicated location field; the summary is the best
            # shot at catching a mentioned city/venue.
            location_raw = entry.get("summary", "") or entry.get("description", "")
            events.append(Event(
                chamber=chamber_name,
                title=title,
                date_raw=raw_date,
                date_parsed=try_parse_date(raw_date) if raw_date else None,
                url=entry.get("link", url),
                source_strategy="rss_feed",
                location_raw=location_raw[:300],
            ))
        if events:
            return events
    return []


# ---------- Strategy 3: schema.org JSON-LD ----------

def strategy_jsonld(chamber_name: str, events_url: str) -> list[Event]:
    resp = fetch(events_url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    events = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        blocks = data if isinstance(data, list) else [data]
        for block in blocks:
            graph = block.get("@graph") if isinstance(block, dict) else None
            candidates = graph if graph else [block]
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                if c.get("@type") not in ("Event", ["Event"]):
                    continue
                title = c.get("name", "").strip()
                start = c.get("startDate", "")
                url = c.get("url", events_url)
                if not title:
                    continue

                loc = c.get("location", {})
                if isinstance(loc, dict):
                    addr = loc.get("address", "")
                    if isinstance(addr, dict):
                        addr = " ".join(str(v) for v in addr.values() if v)
                    location_raw = " ".join(str(v) for v in [loc.get("name", ""), addr] if v)
                elif isinstance(loc, list) and loc:
                    first = loc[0]
                    location_raw = first.get("name", "") if isinstance(first, dict) else str(first)
                else:
                    location_raw = str(loc) if loc else ""

                events.append(Event(
                    chamber=chamber_name,
                    title=title,
                    date_raw=start,
                    date_parsed=try_parse_date(start) if start else None,
                    url=url,
                    source_strategy="jsonld_event",
                    location_raw=location_raw,
                ))
    return events


# ---------- Strategy 4: generic heuristic ----------

def strategy_generic(chamber_name: str, events_url: str) -> list[Event]:
    resp = fetch(events_url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    events = []
    seen_titles = set()

    # Look at headings and links; for each, check if a date pattern appears
    # in the element itself or its immediate siblings/parent.
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "a"]):
        text = tag.get_text(" ", strip=True)
        if not text or len(text) < 4 or len(text) > 200:
            continue

        # search nearby text (self, parent, next siblings) for a date
        search_zone = text
        if tag.parent:
            search_zone += " " + tag.parent.get_text(" ", strip=True)[:300]

        match = DATE_PATTERN.search(search_zone)
        if not match:
            continue

        title = text
        if title in seen_titles:
            continue

        href = tag.get("href") if tag.name == "a" else None
        if not href:
            link = tag.find("a") if tag.name != "a" else None
            href = link.get("href") if link else events_url

        seen_titles.add(title)
        date_raw = match.group(0)
        events.append(Event(
            chamber=chamber_name,
            title=title,
            date_raw=date_raw,
            date_parsed=try_parse_date(date_raw),
            url=urljoin(events_url, href) if href else events_url,
            source_strategy="generic_heuristic",
            # search_zone (title + nearby parent text) is the best available
            # signal for a venue/city mention near this event block.
            location_raw=search_zone[:300],
        ))

    return events


# ---------- Strategy: custom rule for GACC West (Ibexa DXP) ----------

def strategy_custom_gaccwest(chamber_name: str, events_url: str) -> list[Event]:
    resp = fetch(events_url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    events = []
    # GACC West event cards: a date like "16/07/2026" followed by a heading link.
    for block in soup.find_all(["article", "div"]):
        text = block.get_text(" ", strip=True)
        m = re.search(r"\b\d{2}/\d{2}/\d{4}\b", text)
        if not m:
            continue
        heading = block.find(["h2", "h3", "h4"])
        link = block.find("a", href=True)
        if not heading or not link:
            continue
        title = heading.get_text(" ", strip=True)
        if not title:
            continue
        events.append(Event(
            chamber=chamber_name,
            title=title,
            date_raw=m.group(0),
            date_parsed=try_parse_date(m.group(0)),
            url=urljoin(events_url, link["href"]),
            source_strategy="custom_gaccwest",
            location_raw=text[:300],
        ))
    # De-dupe by title
    dedup = {}
    for e in events:
        dedup[e.title] = e
    return list(dedup.values())


def scrape_chamber(chamber: dict) -> tuple[list[Event], str]:
    name = chamber["name"]
    homepage = chamber["homepage"]
    events_url = chamber.get("events_url", homepage)
    platform = chamber.get("platform", "auto")

    print(f"  -> {name} ({events_url})")

    if platform == "custom_gaccwest":
        events = strategy_custom_gaccwest(name, events_url)
        if events:
            return events, "custom_gaccwest"

    if platform == "squarespace":
        events = strategy_squarespace(name, events_url)
        if events:
            return events, "squarespace_json"

    if platform == "auto":
        events = strategy_squarespace(name, events_url)
        if events:
            return events, "squarespace_json"

    events = strategy_rss(name, homepage, events_url)
    if events:
        return events, "rss_feed"

    events = strategy_jsonld(name, events_url)
    if events:
        return events, "jsonld_event"

    events = strategy_generic(name, events_url)
    if events:
        return events, "generic_heuristic"

    return [], "FAILED"


FIELDNAMES = ["chamber", "title", "date_raw", "date_parsed", "location_raw",
              "region", "region_match", "url", "source_strategy"]


def main():
    parser = argparse.ArgumentParser(description="Scrape EuroCham member chamber events.")
    parser.add_argument("--config", default="chambers.yaml")
    parser.add_argument("--chamber", default=None, help="Only scrape chambers whose name contains this substring")
    parser.add_argument("--out", default="output/events.csv")
    parser.add_argument("--include-virtual", action="store_true", default=True,
                         help="Include virtual/online events in the main Bay Area list (default: on)")
    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    chambers = config["chambers"]
    if args.chamber:
        chambers = [c for c in chambers if args.chamber.lower() in c["name"].lower()]
        if not chambers:
            print(f"No chamber matching '{args.chamber}' found in {args.config}")
            sys.exit(1)

    all_events: list[Event] = []
    report_lines = []

    print(f"Scraping {len(chambers)} chamber(s)...\n")
    for chamber in chambers:
        try:
            events, strategy = scrape_chamber(chamber)
        except Exception as e:
            events, strategy = [], f"ERROR: {e}"

        # Classify region for each event using title + whatever location
        # text the strategy managed to capture.
        for e in events:
            region, match = classify_location(e.title, e.location_raw)
            e.region = region
            e.region_match = match

        bay_or_virtual = sum(1 for e in events if e.region in ("bay_area", "virtual"))
        other = len(events) - bay_or_virtual
        status = (f"{chamber['name']}: {len(events)} events via {strategy} "
                  f"({bay_or_virtual} Bay Area/virtual, {other} elsewhere/unclear)")
        print(f"     {status}")
        report_lines.append(status)
        all_events.extend(events)

    bay_area_events = [e for e in all_events if e.region in ("bay_area", "virtual")]
    excluded_events = [e for e in all_events if e.region not in ("bay_area", "virtual")]

    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    def write_csv(path, events):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for e in sorted(events, key=lambda e: e.date_parsed or "9999"):
                writer.writerow(asdict(e))

    write_csv(args.out, bay_area_events)

    excluded_path = args.out.rsplit(".", 1)[0] + "_excluded.csv"
    write_csv(excluded_path, excluded_events)

    json_out = args.out.rsplit(".", 1)[0] + ".json"
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump([asdict(e) for e in all_events], f, indent=2)

    report_path = os.path.join(os.path.dirname(args.out) or ".", "report.txt")
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines))
        failed = [c["name"] for c, l in zip(chambers, report_lines) if "FAILED" in l or "ERROR" in l]
        f.write("\n\n--- Needs manual rule / review (scrape failed) ---\n")
        f.write("\n".join(failed) if failed else "(none)")

        unknown = [e for e in all_events if e.region == "unknown"]
        f.write(f"\n\n--- {len(unknown)} events with no detectable location (kept out of main list, check manually) ---\n")
        for e in unknown[:50]:
            f.write(f"{e.chamber} | {e.title} | {e.url}\n")

    print(f"\nDone. {len(bay_area_events)} Bay Area/virtual events written to {args.out}")
    print(f"{len(excluded_events)} other-location events written to {excluded_path} (for review, not dropped)")
    print(f"Full unfiltered set: {json_out}")
    print(f"Per-chamber report: {report_path}")


if __name__ == "__main__":
    main()
