# ArcticSoak.com — U.S. Cold Plunge Climate & Cost Index

GitHub Pages-ready static data site.

## What it contains
- ArcticSoak Score for major U.S. cities
- NOAA 1991–2020 monthly climate normals integration
- recent NOAA Daily Summaries temperature pulse
- EIA monthly residential electricity rate integration
- automatically generated city SEO pages
- national ranking tables
- downloadable CSV dataset
- ice requirement, chiller sizing and operating-cost calculators
- InHouse Wellness cold-plunge product integration

## First deployment
1. Upload every file/folder to the repository root, including `.github`.
2. GitHub → Settings → Pages → Source: **GitHub Actions**.
3. GitHub → Settings → Pages → Custom domain: **arcticsoak.com**.
4. In DNS, point the apex to GitHub Pages and `www` to your GitHub Pages host.
5. Go to Actions → **Update ArcticSoak Index and deploy** → Run workflow.

The site works with bundled seed data before the first refresh.

## EIA API key (recommended)
EIA's API requires an API key for automatic live electricity-price refreshes.

GitHub → Settings → Secrets and variables → Actions → New repository secret

Name it:
`EIA_API_KEY`

NOAA/NCEI Access Data Service requests do not require a repository secret in this build.

If `EIA_API_KEY` is missing, the updater still refreshes NOAA data and retains bundled/previous electricity prices.

## Update schedule
`.github/workflows/update-index.yml` runs every Monday and can also be triggered manually.

## Methodology
See `/methodology/` on the site. The index is a standardized comparison model, not a manufacturer performance claim.
