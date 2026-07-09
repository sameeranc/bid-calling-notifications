# Bid Calling Notifications

A free, public dashboard that checks weekly for new consultancy
opportunities in **data analysis, GIS/spatial, and weather/climate work**,
posted by Sri Lankan institutes and foreign/multilateral agencies.

It runs entirely on GitHub's free tier: a scheduled GitHub Action does the
scanning, and GitHub Pages hosts the dashboard. No server, no hosting cost,
no login for you to check it.

## How it works

- `scraper/main.py` runs once a week (Tuesdays at 13:00/1pm Sri Lanka time), pulling listings from:
  - **World Bank Procurement Notices** (API, filtered to Sri Lanka)
  - **ReliefWeb Jobs** (API, filtered to Sri Lanka)
  - **Sri Lanka Ministry of Finance** procurement notices page
  - **Central Bank of Sri Lanka** tenders page
- Each listing is checked against `config/keywords.yaml`. Only matches are kept.
- Matches are merged into `docs/data.json`, keeping track of which day each
  listing was first seen, so the dashboard can flag what's new.
- `docs/index.html` is the dashboard - it reads `data.json` directly, no backend needed.
- A handful of sources (UNDP, UN Global Marketplace, ADB, JICA, the Sri Lanka
  e-GP portal, and a few SL government sites) render their listings with
  JavaScript, so they can't be reliably auto-scraped. Those show up as
  one-click "quick check" links at the bottom of the dashboard instead - see
  `config/manual_sources.yaml`.

## Setup (one-time, ~10 minutes)

1. **Create a new GitHub repository** (public), e.g. `bid-calling-notifications`.
2. **Push this folder to it:**
   ```
   cd bid-calling-notifications
   git init
   git add .
   git commit -m "Initial setup"
   git branch -M main
   git remote add origin https://github.com/<your-username>/<your-repo>.git
   git push -u origin main
   ```
3. **Enable GitHub Pages:**
   Repo → Settings → Pages → Source: "Deploy from a branch" → Branch: `main`, folder: `/docs` → Save.
   Your dashboard will be live at `https://<your-username>.github.io/<your-repo>/` within a minute or two.
4. **Allow the workflow to commit results back to the repo:**
   Repo → Settings → Actions → General → Workflow permissions → select
   "Read and write permissions" → Save.
   (The workflow already requests this via `permissions: contents: write`,
   but some org/repo defaults still require the toggle above.)
5. **Run it once manually** to check everything works:
   Repo → Actions tab → "Weekly bid scan" → Run workflow.
   Check the run log; then look at `docs/data.json` in the repo (it should
   update) and refresh your Pages URL.

After that, it runs automatically every Tuesday at 13:00 (1pm) Sri Lanka time -
nothing more to do.

## Customizing

- **Keywords**: edit `config/keywords.yaml` directly on GitHub (or locally +
  push). No code changes needed. Takes effect on the next scheduled run.
- **Manual-check links**: edit `config/manual_sources.yaml` the same way.
- **Schedule**: edit the `cron` line in `.github/workflows/daily-scan.yml`
  (time is in UTC; Sri Lanka is UTC+5:30).
- **Add a new automated source**: add a `fetch_*` function to
  `scraper/sources.py` returning the standard dict shape (see the docstring
  at the top of that file) and register it in `ALL_SOURCES`.

## Adding email notifications later

The dashboard covers the "check weekly" use case for free with zero
maintenance. If you'd also like an email digest on the same schedule, the cleanest free
option is to add a step to `.github/workflows/daily-scan.yml` that reads
`docs/data.json`, and either:

- Sends via a transactional email API (e.g. Resend, Mailgun, Brevo all have
  free tiers) using a secret API key stored in Repo → Settings → Secrets and
  variables → Actions, or
- Sends via Gmail SMTP with an
  [app password](https://support.google.com/accounts/answer/185833) stored
  the same way.

Say the word and this can be added - it just needs your preferred email
provider and the address(es) to send to.

## Known limitations

- Sri Lankan government sites (Treasury, Central Bank) don't have a
  consistent structure or a stable API, so their scrapers are best-effort:
  they may occasionally miss a notice or need small selector tweaks if the
  site redesigns. The World Bank and ReliefWeb sources are far more
  reliable since they're proper APIs.
- Sites that require JavaScript to render results (UNDP, UNGM, ADB, JICA,
  Sri Lanka's e-GP portal) are not auto-scraped - they're listed as
  quick-check links instead. Automating them would require a headless
  browser (e.g. Playwright) running in the Action, which is possible to add
  later if this becomes a priority.
- The Sri Lanka Department of Census & Statistics, Disaster Management
  Centre, Meteorology Department, and Survey Department don't currently
  publish tenders at a stable, scrapable URL - they're included as
  quick-check links pointing to their main sites.
