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
        \b\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]
