#!/usr/bin/env python3
"""
Fetches chamber newsletters from Gmail using a stored refresh token
(no browser, no live login — safe to run unattended in GitHub Actions).

Reads three environment variables (set as GitHub Secrets):
    GMAIL_CLIENT_ID
    GMAIL_CLIENT_SECRET
    GMAIL_REFRESH_TOKEN

Returns a list of dicts: {sender, subject, date, plaintextBody}
which newsletter_parser.extract_events_from_text() can consume directly.
"""

import base64
import os
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Same domain list as before — extend as David subscribes to more
# newsletters. Keep in sync with chambers.yaml.
NEWSLETTER_SENDERS = [
    "babcsf.org", "belwest.org", "belcham.org", "gaba-network.org",
    "gaccwest.com", "faccsf.com", "facccalifornia.com", "sacc-sf.org",
    "saccsf.com", "daccncal.com", "amhuchamber.com",
    "irishnetworkbayarea.com", "advantageaustria.org", "racc.ro",
    "slovakpro.org", "usptc.org", "baia-network.org",
    "californiaspainchamber.org", "americanslovenianclub.org",
    "czechinvest.gov.cz", "portugalglobal.pt",
]


def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GMAIL_CLIENT_ID"],
        client_secret=os.environ["GMAIL_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("gmail", "v1", credentials=creds)


def build_query(days_back: int = 10) -> str:
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
    sender_clause = " OR ".join(f"from:{s}" for s in NEWSLETTER_SENDERS)
    return (f"({sender_clause}) after:{since} "
            f'"unsubscribe" -subject:(board OR "financial update" OR meeting OR minutes)')


def _extract_plaintext(payload) -> str:
    """Walk a Gmail message payload and pull out plaintext body,
    falling back to stripping HTML if no text/plain part exists."""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []):
        text = _extract_plaintext(part)
        if text:
            return text

    if payload.get("mimeType") == "text/html" and "data" in payload.get("body", {}):
        import re
        html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
        return re.sub("<[^<]+?>", " ", html)

    return ""


def fetch_newsletters(days_back: int = 10) -> list[dict]:
    service = get_gmail_service()
    query = build_query(days_back)

    results = service.users().messages().list(userId="me", q=query, maxResults=50).execute()
    messages = results.get("messages", [])

    out = []
    for m in messages:
        msg = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        out.append({
            "sender": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "plaintextBody": _extract_plaintext(msg["payload"]),
        })
    return out


if __name__ == "__main__":
    for n in fetch_newsletters():
        print(n["sender"], "|", n["subject"])
