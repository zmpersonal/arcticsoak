# ArcticSoak.com — U.S. Cold Plunge Energy & Cost Data

GitHub Pages-ready static data site.

## What it contains
- Three-scenario energy and cost estimates for 86 U.S. cities
- NOAA 1991–2020 monthly low/high climate-normal integration
- explicit verified/provisional status on every city record
- EIA monthly state residential electricity-rate integration
- automatically generated city and state reference pages
- versioned CSV, JSON, per-city JSON and permanent release snapshots
- Dataset/DataCatalog structured data, `llms.txt` and crawlable tables
- ice, chiller sizing, running-cost and ice-vs-chiller calculators
- methodology, research, corrections, standards and model changelog pages
- one separated recommended-retailer resource

## First deployment
1. Upload every file/folder to the repository root, including `.github`.
2. GitHub → Settings → Pages → Source: **GitHub Actions**.
3. GitHub → Settings → Pages → Custom domain: **arcticsoak.com**.
4. In DNS, point the apex to GitHub Pages and `www` to your GitHub Pages host.
5. Go to Actions → **Update ArcticSoak Index and deploy** → Run workflow.

The site works with bundled seed data before the first refresh. Seed records are
visibly marked `provisional` and are never represented as NOAA observations.

## EIA API key (recommended)
EIA's API requires an API key for automatic live electricity-price refreshes.

GitHub → Settings → Secrets and variables → Actions → New repository secret

Name it:
`EIA_API_KEY`

NOAA/NCEI Access Data Service requests do not require a repository secret in this build.

If `EIA_API_KEY` is missing, the updater still refreshes NOAA data and retains
disclosed bundled/previous electricity prices.

## Update schedule
`.github/workflows/update-index.yml` runs every Monday and can also be triggered
manually. Each run rebuilds the homepage, city/state pages, rankings, data portal,
annual report, sitemap and dated dataset snapshot.

## Methodology
See `/methodology/`. Model 2.0.0 is a standardized comparison model, not measured
appliance performance. Material changes require a model-version increment and a
changelog entry.
