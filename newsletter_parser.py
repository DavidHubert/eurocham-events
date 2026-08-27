#!/usr/bin/env python3
"""
EuroCham newsletter events extractor.

Chamber newsletters bury one or two real events inside a lot of sponsor
content, policy updates, and press mentions. This looks for the patterns
newsletters actually use to announce events:

  1. Labelled blocks: "Date: <date> Time: <time> Location: <place> ..."
     (the most common pattern — e.g. BABC's newsletters)
  2. "SAVE THE DATE" announcements, which often have a date but no
     Time:/Location: labels yet (details "to follow")
  3. A fallback: any date-pattern match, checked against Bay Area/virtual
     keywords the same way the website scraper does

It reuses DATE_PATTERN and classify_location from scraper.py so an event
found in a newsletter is scored by the exact same rules as one found on a
chamber's website — same output shape, same Bay-Area filtering.

Usage as a library (this is what the Gmail-connected run will call):

    from newsletter_parser import extract_events_from_text
    events = extract_events_from_text(
        chamber="BABC", source_subject=msg["subject"],
        source_date=msg["date"], text=msg["plaintextBody"],
    )

Standalone test:

    python newsletter_parser.py sample.txt
"""

import re
import sys
from dataclasses import dataclass, asdict
from typing import Optional

# Reuse the same date pattern and region classifier as the website scraper,
# so newsletter events are judged by identical Bay Area rules.
from scraper import DATE_PATTERN, classify_location, try_parse_date

LABELLED_BLOCK = re.compile(
    r"Date:\s*(?P<date>.{4,60}?)\s*"
    r"(?:Time:\s*(?P<time>.{2,40}?)\s*)?"
    r"(?:Location:\s*(?P<location>.{4,300}?)\s*)?"
    r"(?=Pricing:|REGISTER|RSVP|Time:|Location:|SAVE THE DATE|Date:|"
    r"EXPRESSION OF INTEREST|EVENT PAGE|MORE DETAILS|VIEW EVENT|"
    r"MORE INFO|LEARN MORE|$)",
    re.IGNORECASE,
)

SAVE_THE_DATE = re.compile(
    r"SAVE THE DATE!?\s*(?P<date>[^.]{4,60}?)(?:\.|$|This is)",
    re.IGNORECASE,
)


@dataclass
class NewsletterEvent:
    chamber: str
    title: str
    date_raw: str
    date_parsed: Optional[str]
    location_raw: str
    region: str
    region_match: str
    source: str  # e.g. "newsletter: Summer Newsletter (2026-08-20)"


def _title_before(text: str, idx: int, window: int = 200) -> str:
    """Best-effort title: prefer the text right after the nearest ALL-CAPS
    section header before this match (newsletters use these as section
    labels — "NEW TO THE BABC EVENTS CALENDAR" — right before event details).
    Falls back to the last sentence-ish chunk in the window."""
    chunk = text[max(0, idx - window):idx].strip()

    caps_headers = list(re.finditer(r"\b[A-Z][A-Z0-9 &'-]{6,60}\b", chunk))
    if caps_headers:
        after_header = chunk[caps_headers[-1].end():].strip(" .!?")
        if after_header and len(after_header) > 8:
            return after_header[:180]
        return caps_headers[-1].group(0).title()

    parts = re.split(r"(?<=[.!?])\s+", chunk)
    candidate = parts[-1].strip() if parts else chunk
    return candidate[:180] if candidate else "(untitled event)"


def extract_events_from_text(chamber: str, source_subject: str, source_date: str, text: str) -> list[NewsletterEvent]:
    if not text:
        return []
    text = re.sub(r"\s+", " ", text)  # newsletters often collapse to one line
    events = []
    source_label = f"newsletter: {source_subject} ({source_date[:10] if source_date else 'unknown date'})"

    seen_spans = []

    for m in LABELLED_BLOCK.finditer(text):
        date_raw = (m.group("date") or "").strip()
        location_raw = (m.group("location") or "").strip()[:180]
        if not date_raw or not DATE_PATTERN.search(date_raw):
            continue
        title = _title_before(text, m.start())
        region, match = classify_location(title, location_raw)
        events.append(NewsletterEvent(
            chamber=chamber,
            title=title,
            date_raw=date_raw,
            date_parsed=try_parse_date(date_raw),
            location_raw=location_raw,
            region=region,
            region_match=match,
            source=source_label,
        ))
        seen_spans.append((m.start(), m.end()))

    for m in SAVE_THE_DATE.finditer(text):
        # skip if this date already came from a labelled block nearby
        if any(abs(m.start() - s) < 50 for s, _ in seen_spans):
            continue
        date_raw = (m.group("date") or "").strip()
        if not DATE_PATTERN.search(date_raw):
            continue
        title = _title_before(text, m.start())
        region, match = classify_location(title, "")
        events.append(NewsletterEvent(
            chamber=chamber,
            title=title,
            date_raw=date_raw,
            date_parsed=try_parse_date(date_raw),
            location_raw="",
            region=region,
            region_match=match,
            source=source_label,
        ))

    return events


def main():
    if len(sys.argv) < 2:
        print("Usage: python newsletter_parser.py <text_file> [chamber_name]")
        sys.exit(1)
    path = sys.argv[1]
    chamber = sys.argv[2] if len(sys.argv) > 2 else "Test Chamber"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    events = extract_events_from_text(chamber, "Test Subject", "2026-08-20", text)
    for e in events:
        print(asdict(e))


if __name__ == "__main__":
    main()
