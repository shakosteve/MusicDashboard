# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal Spotify listening dashboard ("The Listening Booth"). One Python script, `musicdashboard.py`, collects listening data into `data/` and builds `docs/data/listening.js`. A static site in `docs/` renders it. There are no tests, linter, or build tooling. The only dependency is `requests`.

## Commands

```
python musicdashboard.py auth                one-time browser sign-in; writes SPOTIFY_REFRESH_TOKEN to .env
python musicdashboard.py sync                save new recently-played tracks + the podcast episode playing now
python musicdashboard.py import [--limit N]  convert the privacy export in data/export/ into history files
python musicdashboard.py art                 fetch cover art/years for live-synced albums missing them
python musicdashboard.py build               write docs/data/listening.js
python musicdashboard.py wrapped [YEAR ...]  write docs/data/wrapped/<year>.js (default: every year) for docs/wrapped.html?year=YEAR
python musicdashboard.py report [--dry-run|--force]  daily summary email of data/sync.log, listening and import progress
sync.bat                                     the scheduled job: sync, import --limit 20 (until data\import.done exists), build, report
```

- To preview the site, open `docs/index.html` directly in a browser. The data is a `<script>` that sets `window.LISTENING`, not fetched JSON, so the page works from `file://` with no server.
- `sync.bat` hardcodes the Python path (`...\Python313\python.exe`) and appends all output to `data\sync.log`.
- `report` runs on every scheduled run but sends only on the first run from `REPORT_HOUR` (default 8 AM), then writes the date to `data/report.sent`. It does nothing until `SMTP_USER` and `SMTP_PASSWORD` (a Gmail app password) are in `.env`. It parses the `=== <date> <time>` run headers that `sync.bat` writes into the log, so keep that format if you edit `sync.bat`. The owner prefers this email to progress reports in chat.
- The Windows Task Scheduler task `\MusicDashboard` runs `sync.bat` every 30 minutes, launched as `conhost.exe --headless "C:\code\MusicDashboard\sync.bat"` so no console window pops up. Enabling or disabling it needs an elevated prompt (`schtasks /change /tn MusicDashboard /enable`); from a non-admin shell it fails with "Access is denied".

## Data flow

Everything in `data/` is personal and gitignored, as is `.env`, which holds the Spotify app credentials and the refresh token. Spotify can rotate the refresh token; `api_session()` saves the new one.

The data has two sources, which `build()` merges:

1. **Live sync**, written to `recently_played.jsonl` and `episodes.json`.
   - Spotify's recently-played endpoint returns only the last 50 tracks and never podcasts, so `sync` has to run often.
   - Episodes are caught only by polling what's playing now.
   - Plays are deduped by `played_at`.
2. **Privacy export ("Extended streaming history")**: the `Streaming_History_Audio_*.json` files, unzipped into `data/export/`.
   - `import` keeps streams of at least 30s (`MIN_PLAY_MS`).
   - It writes music plays to `history.jsonl`, in the same record shape as `recently_played.jsonl` plus `ms_played`.
   - It writes podcast totals per show to `podcast_history.json`.
   - The export has no album or artist URIs. `import` looks up **one track per (album artist, album name)**, most-played album first, and the album's other tracks borrow that metadata.
   - Lookups are cached in `tracks.json`, where `null` means the lookup failed and another track from that album is tried next run. One episode per show is looked up to get the show URI and art.
   - `import` writes `data/import.done` once nothing is left to look up. Delete it if a new export is unzipped.

`build()` uses the export history and appends live plays only where `played_at[:19]` is after the export's last timestamp. Hours come from `ms_played` when present, otherwise `duration_ms`. Album art and release years come from `art.json`, keyed by album or show URI. A play with no `album_uri` counts toward artists but not toward albums or decades.

## Spotify API constraints

- The app is in development mode. Catalog lookups (`/tracks/{id}`, `/episodes/{id}`) have a daily quota of roughly 500 calls. When it runs out, Spotify returns `429 QUOTA_EXCEEDED` with a `Retry-After` of about 24 hours.
- The player endpoints that `sync` uses kept working while the catalog quota was exhausted.
- `fetch()` sleeps through short 429s, but raises `RateLimited` when `Retry-After` is over 120s. `import` catches it and saves its progress, and `main()` turns it into a clean exit. Never let code sleep through a long `Retry-After`.
- Look items up one at a time; batch catalog endpoints aren't relied on. Keep the `delay` between calls.
- Cover art is hotlinked from `i.scdn.co`, never downloaded. Spotify's terms require it to be shown unaltered and linked back to Spotify, so every card in the site links to `open.spotify.com`.

## Site

`docs/app.js` is a single IIFE that renders `window.LISTENING` into the static sections of `docs/index.html`. Any new key added to the `site` dict in `build()` needs matching markup in `index.html`, and its section should stay `hidden` until there's data.

The current design is a deliberately plain placeholder while the owner decides on a design: ranked lists, a decade table, system fonts, and light/dark tokens on `:root`. Don't invest in restyling it unless asked. The earlier record-shop design (tilted sleeve cards, crates, stickers) is kept in `design/record-shop/`. It reads the same `window.LISTENING` data, except for `top_shows`.

`docs/wrapped.html` is a self-contained "year in review" story page with its CSS and JS inline. It's deliberately dark, made of full-screen cards with custom SVG and CSS charts. It loads `data/wrapped/index.js` (the list of available years) and then `data/wrapped/<year>.js` by script injection, so it also works from `file://`.
- `wrapped()` uses local calendar years via `astimezone()` and counts minutes from `ms_played`.
- Its podcast stats come only from the export.
- "Discoveries" are artists whose first-ever play falls in that year.
- Card links (`#streak`) are followed after render. Headless screenshots of a scrolled card come out blank, so to check a card, hide the other cards instead of scrolling to it.
