# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal Spotify listening dashboard ("The Listening Booth"). One Python script, `musicdashboard.py`, collects listening data into `data/` and builds `docs/data/listening.js`. A static site in `docs/` renders it. There are no tests, linter, or build tooling. The only dependency is `requests`.

## Commands

```
python musicdashboard.py auth                one-time browser sign-in; writes SPOTIFY_REFRESH_TOKEN to .env
python musicdashboard.py sync                save new recently-played tracks + podcast episodes (playing now, or found via resume points)
python musicdashboard.py import [--limit N]  convert the privacy export in data/export/ into history files
python musicdashboard.py art                 fetch cover art/years for live-synced albums missing them
python musicdashboard.py build               write docs/data/listening.js
python musicdashboard.py wrapped [YEAR|YEAR-YEAR ...]  write docs/data/wrapped/<period>.js for docs/wrapped.html?year=<period> (default: see below)
python musicdashboard.py report [--dry-run|--force]  daily summary email of data/sync.log, listening and import progress
python musicdashboard.py playlist "NAME"     save one of the owner's playlists (by name, ID or link) to playlists/<name>.json + .csv + .xspf, with ISRCs; ~1 request per 50 tracks
python musicdashboard.py backup              once a day: re-save + commit + push the playlists in playlists/playlists.json that changed
python musicdashboard.py sharecard            redraw docs/images/share.png, the 1200x630 social preview, from listening.js totals (headless Edge)
python musicdashboard.py publish             commit + push docs/data/ if it changed beyond the "generated" timestamp
sync.bat                                     the scheduled job: sync, import --limit 20 (until data\import.done exists), backup, build, publish, report
```

- The repo is public at github.com/shakosteve/MusicDashboard. GitHub Pages serves `docs/` from `main` at https://shakosteve.github.io/MusicDashboard/.
- `publish` runs on every scheduled run. It commits only `docs/data/` (so code edits stay out), uses `git diff -I` to skip changes that are only the timestamp, then pulls with `--rebase` and pushes, with git prompts disabled. Set `AUTO_PUBLISH=off` in `.env` to stop it.
- Code changes still need committing and pushing by hand. Expect frequent "Listening data through …" commits from the scheduled task on `origin/main`, so pull before pushing.
- To preview the site, open `docs/index.html` directly in a browser. The data is a `<script>` that sets `window.LISTENING`, not fetched JSON, so the page works from `file://` with no server.
- `sync.bat` hardcodes the Python path (`...\Python313\python.exe`) and appends all output to `data\sync.log`.
- `report` runs on every scheduled run but sends only on the first run from `REPORT_HOUR` (default 8 AM), then writes the date to `data/report.sent`. It does nothing until `SMTP_USER` and `SMTP_PASSWORD` (a Gmail app password) are in `.env`. It parses the `=== <date> <time>` run headers that `sync.bat` writes into the log, so keep that format if you edit `sync.bat`. The owner prefers this email to progress reports in chat.
- When the latest run's log has `token request failed (400|401)`, the report leads with "sign in to Spotify again" and step-by-step `auth` instructions (`reauth_note()`). Spotify refresh tokens have no fixed expiry, so this is triggered by rejection, not by a date. `auth` records the sign-in date as `SPOTIFY_AUTHORIZED` in `.env`.
- The Windows Task Scheduler task `\MusicDashboard` runs `sync.bat` every 30 minutes, launched as `conhost.exe --headless "C:\code\MusicDashboard\sync.bat"` so no console window pops up. Enabling or disabling it needs an elevated prompt (`schtasks /change /tn MusicDashboard /enable`); from a non-admin shell it fails with "Access is denied".

## Data flow

`playlists/` is the exception to personal data being gitignored. The owner wants it committed as an off-computer backup, so a playlist ("My Favs" is the important one) can be moved to another service later.
- Each track has its ISRC, the recording's ID on every service.
- The `.csv` uses the columns that transfer services accept as an upload.
- The `.xspf` (`playlist_xspf()`) is the open XSPF playlist format, for players such as VLC and as a self-describing archive.
  - Tracks have no `<location>`, since there's no file. They're described by title, artist, album and duration, and by two `<identifier>`s: `isrc:<code>` (a convention, not part of the standard) and the Spotify URI.
  - Streaming services and transfer tools don't import XSPF, which is why the CSV stays.
- `backup` keeps it current. It runs every scheduled run but works once a day per playlist (dates in `data/playlist_checks.json`).
  - The playlists to back up are listed, by Spotify ID, in the committed `playlists/playlists.json` (`{"playlists": [{"id", "name"}]}`). It's not in `.env`, at the owner's request.
    - Tracking by ID, never by name, means the owner can rename a playlist. `id` can also be a `spotify:playlist:` URI or a link. `name` is only a label, which `backup` keeps current and commits.
    - A missing file or an unknown ID prints "Playlist backup failed: …", which the report flags.
  - A playlist's file is the existing `playlists/*.json` with its `uri` (`playlist_file()`), so a rename keeps `my-favs.json` and its git history. The rename shows as "renamed from 'Old'".
  - It compares the playlist's `snapshot_id` from `/playlists/{id}` (one request) with the last one seen, and downloads the tracks only when it changed.
  - It writes and commits only when the content changed, ignoring `fetched`/`snapshot_id`, and commits only `playlists/` (pathspec), e.g. "My Favs: 3 added, 1 removed", with the tracks in the body. `AUTO_PUBLISH=off` skips the commit.
  - It prints `Playlist <name> changed: …` with `  + `/`  - ` lines, or `Playlist <name> checked: no changes (N tracks)`. `report` shows these under "Playlist backup", one line per playlist even when unchanged, and "not checked today" for an entry whose date in `playlist_checks.json` isn't today. The report's problem scan skips those lines, since track names can contain words like "Warning".

**`data/` is a junction into OneDrive** (`C:\Users\zamdr\OneDrive\MusicDashboard_Data`, pinned "always keep on this device"), following GoneWalking, so it's backed up off the computer. It holds irreplaceable state: live plays older than Spotify's last 50, `episodes.json`, the export, and lookups that cost days of API quota. Don't delete or bulk-rewrite it. GoneWalking's `music.py` reads it through the junction as `../MusicDashboard/data`.

Everything in `data/` is personal and gitignored, as is `.env`, which holds the Spotify app credentials and the refresh token. Spotify can rotate the refresh token; `api_session()` saves the new one.

The data has two sources, which `build()` merges:

1. **Live sync**, written to `recently_played.jsonl` and `episodes.json`.
   - Spotify's recently-played endpoint returns only the last 50 tracks and never podcasts, so `sync` has to run often.
   - Episodes come from two places. The first is polling what's playing now.
   - The second is `sync_shows()`, which reads each recently heard show's 10 latest episodes and their `resume_point` (via the `user-read-playback-position` scope), so episodes heard between runs are caught.
     - Shows heard in the last 7 days are checked every 2 hours, and those heard in the last 30 days once a day, at most 8 per run. Check times are kept in `show_checks.json`.
     - A found episode is dated by when it was found, except on a show's first check, when it gets its release date. A first check never re-dates episodes that are already recorded.
     - Episodes with under 10s of progress (`MIN_EPISODE_MS`) are skipped by `sync_shows()` and hidden by `build()` and `report`. The poll still records them, because the episode may have only just started.
     - A show not heard in the last 30 days isn't checked, so a new or dormant show is only picked up once currently-playing sees it.
   - Plays are deduped by `played_at`.
   - 47 plays from Oct 26 to Nov 3, 2026 that sync missed (mostly before the scheduled task started) were backfilled from the Account data export (`data/account/StreamingHistory_music_*.json`).
     - They carry `"source": "account_export"` and `ms_played`.
     - Their `played_at` is only to the minute (`…T18:15:00Z`), because that export has no seconds.
     - Their song details were copied from known plays of the same title and lead artist, or found by `/search` for 4 songs.
     - The pre-backfill file is kept as `data/recently_played.jsonl.before-backfill`.
2. **Privacy export ("Extended streaming history")**: the `Streaming_History_Audio_*.json` files, unzipped into `data/export/`.
   - `import` keeps streams of at least 30s (`MIN_PLAY_MS`).
   - It writes music plays to `history.jsonl`, in the same record shape as `recently_played.jsonl` plus `ms_played`.
   - It writes podcast totals per show to `podcast_history.json`.
   - The export has no album or artist URIs. `import` looks up **one track per (album artist, album name)**, most-played album first, and the album's other tracks borrow that metadata.
   - Lookups are cached in `tracks.json`, where `null` means the lookup failed and another track from that album is tried next run. One episode per show is looked up to get the show URI and art.
   - `import` writes `data/import.done` once nothing is left to look up. Delete it if a new export is unzipped.

`build()` uses the export history and appends live plays only where `played_at[:19]` is after the export's last timestamp. Hours come from `ms_played` when present, otherwise `duration_ms`. Album art and release years come from `art.json`, keyed by album or show URI. A play with no `album_uri` counts toward artists but not toward albums or decades.

## Spotify API constraints

- The app is in development mode. Catalog lookups (`/tracks/{id}`, `/episodes/{id}`, and presumably `/shows/{id}/episodes`) have a daily quota of roughly 500 calls. When it runs out, Spotify returns `429 QUOTA_EXCEEDED` with a `Retry-After` of about 24 hours.
- The player endpoints that `sync` uses (recently-played, currently-playing) kept working while the catalog quota was exhausted.
- `sync_shows()` uses up to 8 catalog calls per run, roughly 150–250 a day, so it shares the quota with `import`. On `RateLimited` it stops and saves without failing the sync.
- `fetch()` sleeps through short 429s, but raises `RateLimited` when `Retry-After` is over 120s. `import` catches it and saves its progress, and `main()` turns it into a clean exit. Never let code sleep through a long `Retry-After`.
- Look items up one at a time; batch catalog endpoints aren't relied on. Keep the `delay` between calls.
- Cover art is hotlinked from `i.scdn.co`, never downloaded. Spotify's terms require it to be shown unaltered and linked back to Spotify, so every card in the site links to `open.spotify.com`.

## Site

`docs/app.js` is a single IIFE that renders `window.LISTENING` into the static sections of `docs/index.html`. Any new key added to the `site` dict in `build()` needs matching markup in `index.html`, and its section should stay `hidden` until there's data.

The live design is a retro car stereo, chosen by the owner. They turned down the record-shop theme as too canned, so keep the objects realistic and functional rather than costume-like. The design is dark only.
- **Head unit:** the last played cover in the slot, and an amber display with the track. Long titles scroll across it.
- **Presets:** the six buttons link to the sections and to `wrapped.html` (Year). The Talk and Year buttons are removed when there's no data for them.
- **Odometer:** rolling digit drums for the totals.
- **Mixtape insert:** recent plays 2–17, handwritten as Side A and Side B.
- **8-tracks:** top artists, with a tape bar showing plays relative to #1.
- **Cassettes:** top albums. The art is the J-card in the case, the tape slides out on hover, and the spine is `contain: size` so long titles don't stretch the case.
- **Coming back for seconds:** the 8 most-replayed songs, each with a rewind counter, built from `site.replays`.
  - `replay_stats()` reads the raw export, which has `ms_played` and `reason_start`/`reason_end`. It counts three kinds of replay:
    - **ran it back:** one play of 1.5 times the song's length or more, counted as extra song-lengths. A song's length is the median of its finished plays, so no API calls are needed.
    - **went back:** the song started again by back, click or play within 3 plays and 20 minutes.
    - **right after:** finished, then played again.
  - Plays split by `logout`→`appload` are resumes, not replays.
  - The result is cached in `data/replays.json` until the export changes. It covers only the export's range, since live sync has no play lengths or reasons.
  - Most real replays are of the "ran it back" kind, invisible as separate plays. The owner confirmed the results match their memory (Dorothy's two songs at the top).
- **Graphic equalizer:** decades, 12 LEDs per band. Clicking a band shows its covers. The 1900 placeholder release year is left out.
- **AM dial:** top shows, whose needle follows the hovered preset, and recent episodes.

`index.html` loads the data and `data/wrapped/index.js` with a unique `?t=` URL, because Pages caches for 10 minutes. It then loads `app.js`, and reloads every 10 minutes while the page is visible. The loader reads `../../docs/data/` when the path contains `/design/`, so a copy can be previewed under `design/`.

**Social previews:** `index.html` and `wrapped.html` carry static `og:`/`twitter:` tags, following the GoneWalking pattern. Both point at `images/share.png`.
- The card is a stereo faceplate with the odometer totals, deliberately without album art: Spotify art must link back to Spotify, and a preview image can't.
- It's drawn on demand by `sharecard`, not on every build, so the repo doesn't gain a new binary every 30 minutes. Rerun it when the totals have drifted.
- Link previews never run JavaScript, so `wrapped.html?year=2024` previews the same as the plain page.

Earlier designs are kept for reference in:
- `design/plain/`: the placeholder ranked-list design.
- `design/record-shop/`: the record-shop design.

Their pages load `data/listening.js` directly, so they need copying into `docs/` to preview.

`docs/wrapped.html` is a self-contained "year in review" story page with its CSS and JS inline. It's deliberately dark, made of full-screen cards with custom SVG and CSS charts. It loads `data/wrapped/index.js` (the list of available years) and then `data/wrapped/<year>.js` by script injection, so it also works from `file://`.
- `wrapped()` uses local calendar years via `astimezone()` and counts minutes from `ms_played`.
- A review covers a year, or a span such as `2013-2019`.
  - By default, every year from the first one with `WRAPPED_MIN_PLAYS` (1,000) plays gets its own review, and the sparse years before it share one (currently 2013–2019).
  - `WRAPPED_YEARS` holds numbers for years and strings for spans.
  - A span's data has `span`, `label`, `period_days` and `by_year` (minutes and days per year). In `wrapped.html` a span gets bars per year instead of the monthly bars and the calendar, no "vs last year" and no rank badges, and dates that include the year.
  - The discoveries card is skipped when every artist counts as new.
- Its podcast stats come only from the export.
- "Discoveries" are artists whose first-ever play falls in that year.
- Card links (`#streak`) are followed after render. Headless screenshots of a scrolled card come out blank, so to check a card, hide the other cards instead of scrolling to it.
