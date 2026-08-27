#!/usr/bin/env python3
"""
ONE-TIME SETUP SCRIPT — run this on your own computer, not in GitHub Actions.

This opens a browser, asks you to log into Gmail and approve read-only
access, then prints a "refresh token" — a long string that lets the
weekly automated job read your Gmail without you being present, without
ever storing your password.

Before running this:
1. Follow the Google Cloud Console steps in README.md to create OAuth
   credentials, and download the file as `credentials.json` into this
   same folder.

Then run:
    pip install google-auth-oauthlib google-auth google-api-python-client
    python setup_gmail_auth.py

A browser window will open. Log in, approve access. This script will
then print your refresh token — copy it and paste it into GitHub as a
secret called GMAIL_REFRESH_TOKEN (see README.md for exact steps).

You only need to do this once. If you ever revoke access or the token
stops working, just run this again and update the GitHub secret.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

# Read-only access to Gmail — this script (and the weekly job) can never
# send, delete, or modify anything in your inbox, only read it.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n" + "=" * 60)
    print("SUCCESS. Here is your refresh token:")
    print("=" * 60)
    print(creds.refresh_token)
    print("=" * 60)
    print("\nAlso save these two values from credentials.json — you'll")
    print("need all three as GitHub secrets:")
    print(f"  GMAIL_CLIENT_ID     = {creds.client_id}")
    print(f"  GMAIL_CLIENT_SECRET = {creds.client_secret}")
    print(f"  GMAIL_REFRESH_TOKEN = {creds.refresh_token}")
    print("\nSee README.md for how to add these as GitHub Secrets.")


if __name__ == "__main__":
    main()
