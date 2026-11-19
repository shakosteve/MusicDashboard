# Playlist Provenance

**TL;DR:** a personal Spotify project that does two things:

1. **[The Listening Booth](https://shakosteve.github.io/playlist-provenance/)**: a website of what I listen to, styled as a retro car stereo. It shows the last song played, top artists and albums, the songs I keep replaying, music by decade and podcasts. There's also a **[year in review](https://shakosteve.github.io/playlist-provenance/wrapped.html)** for every year back to 2013, and a narrated video for 2025.
2. **A playlist backup with a paper trail.** My favourite playlist and my Liked Songs are saved in this repo, and every change gets its own commit: a song added, removed or moved, and whether I made the change or Spotify did. It also notes when a track goes greyed out in my country. If Spotify ever loses a playlist, or I move to another service, every song can be found again.

It updates itself every 30 minutes, and sends me a summary email each morning.

It's built for one person's account, not as a ready-to-install app, but the code is all here if you want to borrow from it.

---

## The playlist backup

The saved copies are in [`playlists/`](playlists/), each in three formats:

| File | What it's for |
|---|---|
| `.csv` | Uploading to a playlist transfer service, to move the playlist to Apple Music, YouTube Music, Tidal and so on |
| `.json` | The full record: every track's details, when it was added, and its ISRC |
| `.xspf` | The open playlist format, readable by players such as VLC |

Each track has its **ISRC**, the code that identifies a recording on every service. That's how the songs can be matched up anywhere, even after Spotify drops them.

A playlist's **commit history** is its provenance. For example, [the history of `my-favs.json`](https://github.com/shakosteve/playlist-provenance/commits/main/playlists/my-favs.json) shows what changed, when, and who made the change.

[`unavailable.json`](playlists/unavailable.json) keeps a running list of backed-up tracks that Spotify won't play in my country. Licensing usually greys a track out rather than removing it. Each entry records when the track went unplayable and when that ended: playable again, or removed from the playlist.

A saved playlist can also be **restored**: rebuilt on Spotify as a new playlist, or added back into Liked Songs. Where a saved track no longer plays, a playable copy of the same recording is found by its ISRC.

## The site

[`docs/`](docs/) is a static site on GitHub Pages, with no server and no framework.

- **Dashboard** (`index.html`): the head unit (the last track played), the odometer (totals), a mixtape of recent plays, 8-tracks for top artists, cassettes for top albums, the most-replayed songs, a graphic equalizer of decades and an AM dial of podcasts.
- **Year in review** (`wrapped.html?year=2024`): a story of full-screen cards, much like Spotify Wrapped. It covers top songs, artists and albums, listening by month and day, streaks, discoveries, genres and podcasts. The sparse early years, 2013–2019, share one review.
- **Video** (`docs/video/`): a narrated, animated, vertical year in review, made with [Remotion](https://www.remotion.dev/) and Azure's text-to-speech voices. The project is in [`video/`](video/).

All the album art comes from Spotify and links back to it. The social preview image and the video don't use album art for that reason.

---

## How it works

Everything is in one Python script, [`provenance.py`](provenance.py), whose only dependency is `requests`. Windows Task Scheduler runs [`sync.bat`](sync.bat) every 30 minutes, which:

1. **syncs** new plays from Spotify, plus the podcast episode playing now,
2. **imports** a few more tracks from Spotify's privacy export, when there's one to finish,
3. looks up a few more artists' **genres** on MusicBrainz,
4. **backs up** the playlists, and commits and pushes any change,
5. **builds** the site's data (`docs/data/listening.js`) and **publishes** it,
6. sends the **daily report** email, once a day.

### Where the listening data comes from

- **Live sync:** Spotify's recently-played endpoint returns only the last 50 tracks, and never podcasts, so it has to be checked often. Podcast episodes are caught from what's playing now, and from the resume points of shows heard recently.
- **The privacy export** ("Extended streaming history", requested from Spotify's privacy settings) goes back years and records how long each play lasted. That's what the year in review and the replay counts are built from.
- **MusicBrainz** supplies genres, because Spotify doesn't return genres to apps in development mode.

The raw data stays in a local `data/` folder (backed up to OneDrive, not committed). Only the site's summary data and the playlists are public.

### Living with Spotify's limits

The Spotify app is in development mode, which allows roughly 500 catalog lookups a day. So:
- lookups are cached and spread across the day,
- tracks are looked up one album at a time,
- the playlist check costs one request per playlist on most runs, with one full download a day.

When the limit is hit, the script saves its progress and stops cleanly, then carries on the next day.

### Commands

```
python provenance.py auth          sign in to Spotify (once)
python provenance.py sync          save new plays and podcast episodes
python provenance.py import        read the privacy export in data/export/
python provenance.py genres        look up artists' genres on MusicBrainz
python provenance.py build         write the dashboard's data
python provenance.py wrapped       write the year-in-review data
python provenance.py playlist "My Favs"    save one playlist
python provenance.py backup        check the playlists in playlists/playlists.json, commit changes
python provenance.py restore "My Favs" --dry-run    rebuild a saved playlist on Spotify
python provenance.py report        the daily summary email
python provenance.py publish       commit and push the site's data
```

### Running your own copy

This isn't packaged for other people, but it can be adapted:

1. Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) with the redirect URI `http://127.0.0.1:8888/callback`.
2. Put `CLIENT_ID` and `CLIENT_SECRET` in a `.env` file next to the script, then run `pip install -r requirements.txt` and `python provenance.py auth`.
3. Run `sync` on a schedule. `sync.bat` is the Windows version; edit its Python path.
4. Optionally, request the extended streaming history from Spotify, unzip it into `data/export/`, and run `import`.
5. List the playlists to back up in `playlists/playlists.json`, by Spotify ID. Use `"liked"` for Liked Songs.
6. Optional settings: `SMTP_USER` and `SMTP_PASSWORD` (a Gmail app password) for the daily email, `AUTO_PUBLISH=off` to stop the automatic commits.

The site works from `file://`: open `docs/index.html` in a browser after a `build`.

## Credits

- Listening data, album art and links from [Spotify](https://open.spotify.com).
- Genres from [MusicBrainz](https://musicbrainz.org), under CC0.
- The video's backdrops and music are from [Pixabay](https://pixabay.com), credited in [`video/CREDITS.md`](video/CREDITS.md).
