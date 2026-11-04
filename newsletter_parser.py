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

import html as htmllib
import re
import sys
from dataclasses import dataclass, asdict
from typing import Optional

# Reuse the same date pattern and region classifier as the website scraper,
# so newsletter events are judged by identical Bay Area rules.
from scraper import DATE_PATTERN, classify_location, try_parse_date, _infer_year

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
    url: str = ""  # registration / details link, when the newsletter has one


# ---------------------------------------------------------------------------
# Pipe-separated event lines, e.g. GABA's newsletters:
#     November 5 | 5:30 - 8 PM | Palo Alto
#     More information and registration here   <- a link
# The date has no year and the newsletter has no separate title, so the title
# is recovered from the event's own link when possible (see _title_from_link).
# ---------------------------------------------------------------------------

_MONTHS_RE = ("January|February|March|April|May|June|July|August|September|October|"
              "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
PIPE_EVENT = re.compile(
    rf"^[ \t]*(?P<month>{_MONTHS_RE})\.?[ \t]+(?P<day>\d{{1,2}})(?:[ \t]*[-\u2013][ \t]*(?:{_MONTHS_RE})?\.?[ \t]*\d{{1,2}})?"
    r"[ \t]*\|[ \t]*(?P<time>[^|\n]{2,40}?)[ \t]*\|[ \t]*(?P<loc>[^|\n]{2,90}?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_LINK_MARK = re.compile(r"\[\[(https?://[^\]\s]+)\]\]")
_ACRONYMS = {"gaba", "ai", "sf", "us", "usa", "eu", "uk", "dc", "gacc", "facc", "babc", "inba",
             "dacc", "hacc", "sacc", "vc", "ceo", "cto", "ip", "esg", "r&d", "ev", "it"}


def html_to_text_with_links(html: str) -> str:
    """Turn newsletter HTML into plain text with one line per block, and keep
    each link as a [[url]] marker right after its text."""
    if not html:
        return ""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    t = re.sub(r"(?is)<a\b[^>]*?href=[\"']?([^\"' >]+)[^>]*>(.*?)</a>", lambda m: f"{m.group(2)} [[{m.group(1)}]] ", t)
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6]|table)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmllib.unescape(t).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _title_from_link(url: str, timeout: int = 10) -> str:
    """Many newsletter links (GrowthZone 'ap/r/...') redirect to a page whose
    address ends in the event's name, e.g. .../Details/gaba-women-fall-meet-1940875.
    Read the redirect target WITHOUT following it, and turn that slug into a title."""
    try:
        import requests
        r = requests.get(url, allow_redirects=False, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0 (EuroCham events page)"})
        loc = r.headers.get("Location", "")
        m = re.search(r"/Details/([a-z0-9-]+?)(?:-\d{3,})?(?:[/?#]|$)", loc, re.IGNORECASE)
        if not m:
            return ""
        words = [w for w in m.group(1).split("-") if w]
        return " ".join(w.upper() if w.lower() in _ACRONYMS else w.capitalize() for w in words)
    except Exception:
        return ""


def _first_sentence(paragraph: str, limit: int = 110) -> str:
    paragraph = re.sub(r"\[\[[^\]]*\]\]", "", paragraph)
    paragraph = re.sub(r"\s+([.!?,;:])", r"\1", paragraph).strip()
    m = re.match(r"(.+?[.!?])(?:\s|$)", paragraph)
    sentence = m.group(1) if m else paragraph
    if len(sentence) > limit:
        sentence = sentence[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") + "\u2026"
    return sentence.rstrip(".")


def extract_pipe_events(chamber: str, source_label: str, text: str, resolve_titles: bool = True) -> list[NewsletterEvent]:
    """`text` should keep its line breaks (and may contain [[url]] link markers).
    Layout assumed (GABA): [banner link line] / description paragraph(s) /
    'Month D | time | place' / 'More information ... here [link]'."""
    marker_only = re.compile(r"^\s*\[\[https?://[^\]\s]+\]\]\s*$")
    events = []
    prev_end = 0
    for m in PIPE_EVENT.finditer(text):
        month, day = m.group("month"), m.group("day")
        date_raw = f"{month} {day}"
        date_parsed = _infer_year(date_raw, lookback_days=7)
        loc = m.group("loc").strip()

        # The block above this event line: starts after the last link-only line
        segment_lines = [ln.strip() for ln in text[prev_end:m.start()].split("\n") if ln.strip()]
        start = 0
        banner_link = ""
        for i, ln in enumerate(segment_lines):
            if marker_only.match(ln):
                start = i + 1
                banner_link = _LINK_MARK.search(ln).group(1)
        block = [ln for ln in segment_lines[start:] if not _LINK_MARK.fullmatch(ln.strip())]
        block = [ln for ln in block if not (len(ln) < 45 and ln.endswith(":"))]

        # The link after the event line ("More information ... here")
        after_lines = text[m.end(): m.end() + 600].split("\n")[1:3]
        link_after = ""
        for ln in after_lines:
            if PIPE_EVENT.match(ln):
                break
            lm = _LINK_MARK.search(ln)
            if lm:
                link_after = lm.group(1)
                break
        url = link_after or banner_link

        # Title: from the link's destination if possible, else first sentence of the block
        title = _title_from_link(url) if (url and resolve_titles) else ""
        if not title and banner_link and banner_link != url and resolve_titles:
            title = _title_from_link(banner_link)
        if not title:
            title = _first_sentence(block[0]) if block else "Event"
        region, match = classify_location(title, loc)
        events.append(NewsletterEvent(
            chamber=chamber, title=title, date_raw=date_raw, date_parsed=date_parsed,
            location_raw=loc, region=region, region_match=match, source=source_label, url=url,
        ))
        # next event's block starts after this event's trailing link line
        prev_end = m.end() + (sum(len(x) + 1 for x in after_lines[:1]) if link_after else 0)
        tail = text[m.end(): m.end() + 600]
        nl = tail.split("\n")
        consumed = 0
        for k, ln in enumerate(nl[1:3], start=1):
            consumed = sum(len(x) + 1 for x in nl[:k + 1])
            if _LINK_MARK.search(ln):
                break
        prev_end = m.end() + consumed
    return events


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


def extract_events_from_text(chamber: str, source_subject: str, source_date: str, text: str,
                             html: str = "") -> list[NewsletterEvent]:
    if not text and not html:
        return []
    source_label = f"newsletter: {source_subject} ({source_date[:10] if source_date else 'unknown date'})"

    # Pattern A: "Month D | time | place" lines (keeps line breaks and links)
    rich = html_to_text_with_links(html) if html else (text or "")
    events = extract_pipe_events(chamber, source_label, rich)

    # Pattern B/C below work on a single collapsed line of plain text
    text = re.sub(r"\s+", " ", text or re.sub(r"\[\[[^\]]*\]\]", " ", rich))  # newsletters often collapse to one line

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
