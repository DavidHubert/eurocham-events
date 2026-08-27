# EuroCham SF — Bay Area Chamber Events

A weekly-updating page listing upcoming Bay Area events from EuroCham's 20
member chambers — pulled from their websites and their email newsletters.

**Live page** (once set up): `https://davidhubert.github.io/eurocham-events/`

## How it works

Every Monday at 1am Pacific, a GitHub Actions job:
1. Scrapes all 20 chamber websites (`scraper.py`)
2. Reads recent chamber newsletters from Gmail (`gmail_fetch.py`)
3. Extracts events from both, filters to Bay Area + virtual only (`newsletter_parser.py`)
4. Merges everything, removing duplicates (the same joint event announced
   by two chambers becomes one entry, not two)
5. Rewrites `docs/index.html` — the page GitHub Pages serves — and commits it

You never have to run anything yourself once it's set up. You can also
trigger a run manually any time from the **Actions** tab on GitHub.

## One-time setup

You need to do three things, in order. Steps 1–2 are Google's side (giving
the weekly job read-only Gmail access); step 3 is GitHub's side.

### Step 1: Create Google Cloud credentials

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and
   sign in with the Google account whose Gmail you want read (`david@eu-insider.com`)
2. Click the project dropdown at the top → **New Project** → name it
   `eurocham-events` → **Create**
3. Once created, make sure it's selected in the project dropdown
4. In the left sidebar (or search bar at top): go to **APIs & Services** →
   **Library**
5. Search for **Gmail API** → click it → **Enable**
6. Go to **APIs & Services** → **OAuth consent screen**
   - User type: **External** → Create
   - App name: `EuroCham Events`, your email for the two contact fields
   - Click through **Save and Continue** on each remaining screen (Scopes,
     Test users, Summary) without changing anything
   - On the "Test users" screen specifically, click **+ Add Users** and add
     `david@eu-insider.com`
7. Go to **APIs & Services** → **Credentials**
   - Click **+ Create Credentials** → **OAuth client ID**
   - Application type: **Desktop app**
   - Name: `eurocham-events-desktop`
   - Click **Create**
   - Click **Download JSON** on the popup — this file is your `credentials.json`

### Step 2: Get your refresh token (run once, on your own computer)

1. Put the downloaded `credentials.json` file in this project folder
2. Open a terminal in this folder and run:
   ```
   pip install google-auth-oauthlib google-auth google-api-python-client
   python setup_gmail_auth.py
   ```
3. A browser window opens. Log in with `david@eu-insider.com`, and click
   through the "Google hasn't verified this app" warning (this is normal —
   it's your own app, just not submitted for Google's public review, which
   isn't needed since only you use it) → **Continue** → approve the
   Gmail read-only permission
4. Back in the terminal, you'll see three values printed:
   ```
   GMAIL_CLIENT_ID     = ...
   GMAIL_CLIENT_SECRET = ...
   GMAIL_REFRESH_TOKEN = ...
   ```
   Keep this terminal window open — you'll copy these into GitHub next.

**Do not commit `credentials.json` to GitHub** — it's already listed in
`.gitignore` so a normal git push won't include it, but if you're uploading
files manually through the GitHub website, just don't upload that one file.

### Step 3: Add the three secrets to GitHub

1. Go to your repo: `github.com/DavidHubert/eurocham-events`
2. **Settings** tab → left sidebar: **Secrets and variables** → **Actions**
3. Click **New repository secret** three times, once for each value from
   Step 2:
   - Name: `GMAIL_CLIENT_ID` — paste the value — **Add secret**
   - Name: `GMAIL_CLIENT_SECRET` — paste the value — **Add secret**
   - Name: `GMAIL_REFRESH_TOKEN` — paste the value — **Add secret**

Secrets are encrypted — nobody (including you, after saving) can view them
again through the GitHub UI, only overwrite them. That's expected.

### Step 4: Turn on GitHub Pages

1. Still in **Settings** → left sidebar: **Pages**
2. Under "Build and deployment" → Source: **Deploy from a branch**
3. Branch: **main**, folder: **/docs** → **Save**
4. GitHub will show you the URL (something like
   `https://davidhubert.github.io/eurocham-events/`) — this may take a
   minute or two to go live after the first successful run

### Step 5: Run it for the first time

1. Go to the **Actions** tab on your repo
2. Click **Weekly events refresh** in the left list
3. Click **Run workflow** (dropdown on the right) → **Run workflow** button
4. Wait ~1-2 minutes, refresh the page — you'll see a green checkmark when
   it succeeds (or a red X if something needs fixing — click into it to see
   the error, and send it to me)
5. Once green, check `docs/index.html` got updated in the repo, and visit
   your GitHub Pages URL

After that first successful run, it repeats automatically every Monday —
nothing more for you to do.

## Files in this project

- `scraper.py` — scrapes the 20 chamber websites
- `chambers.yaml` — the list of chambers and their URLs (edit this to fix a
  broken URL or add a new chamber)
- `newsletter_parser.py` — extracts events from newsletter email text
- `gmail_fetch.py` — reads matching newsletters from Gmail using the stored
  refresh token (used by the weekly Action)
- `setup_gmail_auth.py` — the one-time local script from Step 2 (never runs
  in GitHub Actions, only on your machine)
- `generate_page.py` — runs the full pipeline and writes `docs/index.html`
- `.github/workflows/weekly.yml` — the schedule definition

## Changing the schedule or adding a chamber

- **Different day/time**: edit the `cron:` line in
  `.github/workflows/weekly.yml`. Format is `minute hour day month weekday`,
  always in UTC.
- **New chamber website**: add an entry to `chambers.yaml`.
- **New chamber newsletter**: add its sending domain to `NEWSLETTER_SENDERS`
  in both `gmail_fetch.py` and `chambers.yaml`'s newsletter section.

## If something breaks

Check the **Actions** tab — every run's log is kept there. The most common
issue is a chamber changing their website layout, which just means that one
chamber returns 0 events until the scraper's rule for it is updated — it
won't break the rest of the page.
