"""MusicDashboard: collect my Spotify listening (music and podcasts) for a personal site.

    python musicdashboard.py auth    one-time: sign in to Spotify in the browser, saves the refresh token to .env
    python musicdashboard.py sync    save new recently played tracks, and podcast episodes (playing now or by resume point)
    python musicdashboard.py art     fetch cover art and release years for saved albums missing them
    python musicdashboard.py import  read the privacy data export unzipped into data/export/
    python musicdashboard.py build   write docs/data/listening.js for the site (docs/index.html)

Settings in .env:

    CLIENT_ID=...                    from the app at developer.spotify.com/dashboard
    CLIENT_SECRET=...
    REDIRECT_URI=http://127.0.0.1:8888/callback   optional (this is the default); must match the app's
                                     Redirect URI exactly
    SPOTIFY_REFRESH_TOKEN=...        written by `auth`; Spotify may replace it on refresh, and `sync`
                                     saves the new one
    SMTP_USER=you@gmail.com          optional, for the daily `report` email; SMTP_PASSWORD is a Gmail
    SMTP_PASSWORD=...                app password. Also optional: REPORT_TO (default SMTP_USER),
                                     REPORT_HOUR (default 8), SMTP_HOST/SMTP_PORT (default Gmail, 587)

Spotify only returns the last 50 played tracks and never includes podcast episodes there, so `sync`
should run often (every 30-60 minutes, via sync.bat in Task Scheduler). Episodes are caught by
checking what's playing at each run, and from the resume points of recently heard shows' latest
episodes (so ones heard between runs show up too). The full history comes from the privacy data export
("Extended streaming history"): unzip its Streaming_History_Audio_*.json files into data/export/ and
run `import`. Plays in recently_played.jsonl after the export's last play are added on top of it.

Data (in data/, not committed):
    recently_played.jsonl   one play per line, oldest first, unique by played_at
    episodes.json           podcast episodes seen playing or with a resume point, by episode URI, with the furthest progress
    show_checks.json        when each show's resume points were last checked, by show URI
    art.json                cover art URLs (i.scdn.co, ~300px and ~640px) by album or show URI
    export/                 the privacy data export's JSON files
    history.jsonl           music plays from the export (30s or more), in the recently_played format
    tracks.json             album and artists of each track in the export, looked up once by `import`
    podcast_history.json    podcast listening from the export, totalled by show

Cover art is linked from Spotify's image server, not copied; Spotify's terms ask that it's shown
unaltered and links back to Spotify.
"""

import argparse
import base64
import json
import os
import re
import secrets
import sys
import time
import webbrowser
import smtplib
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
DATA = ROOT / "data"
PLAYS = DATA / "recently_played.jsonl"
EPISODES = DATA / "episodes.json"
ART = DATA / "art.json"
EXPORT = DATA / "export"
HISTORY = DATA / "history.jsonl"
TRACKS = DATA / "tracks.json"
SHOWS = DATA / "podcast_history.json"
IMPORT_DONE = DATA / "import.done"
SYNC_LOG = DATA / "sync.log"
REPORT_SENT = DATA / "report.sent"
SHOW_CHECKS = DATA / "show_checks.json"
MIN_PLAY_MS = 30_000  # Spotify counts a stream from 30 seconds
MIN_EPISODE_MS = 10_000  # an episode stopped sooner than this wasn't really listened to
API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
DEFAULT_REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = [
    "user-read-recently-played",
    "user-read-currently-playing",
    "user-read-playback-state",
    "user-read-playback-position",
    "user-top-read",
    "user-library-read",
    "user-follow-read",
    "playlist-read-private",
    "playlist-read-collaborative",
]


def load_env(path=ENV):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def save_env_value(key, value, path=ENV):
    """Set key=value in .env, replacing an existing line for that key or appending one."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f"{key}={value}"
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
    if pattern.search(text):
        text = pattern.sub(lambda _: line, text)
    else:
        text = text + ("" if text.endswith("\n") or not text else "\n") + line + "\n"
    path.write_text(text, encoding="utf-8")
    os.environ[key] = value


def require(key):
    value = os.environ.get(key)
    if not value:
        sys.exit(f"{key} is missing from .env")
    return value


def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def token_request(data):
    auth = base64.b64encode(f"{require('CLIENT_ID')}:{require('CLIENT_SECRET')}".encode()).decode()
    r = requests.post(f"{ACCOUNTS}/api/token", data=data,
                      headers={"Authorization": f"Basic {auth}"}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"Spotify token request failed ({r.status_code}): {r.text}\n"
                 "If the refresh token was rejected, run `python musicdashboard.py auth` again.")
    return r.json()


def auth():
    """Sign in once in the browser and save the refresh token."""
    redirect = os.environ.get("REDIRECT_URI") or DEFAULT_REDIRECT
    parsed = urlparse(redirect)
    state = secrets.token_urlsafe(16)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path != parsed.path:
                self.send_response(404)
                self.end_headers()
                return
            result.update({k: v[0] for k, v in parse_qs(url.query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<p>Signed in. You can close this tab.</p>")

        def log_message(self, *args):
            pass

    server = HTTPServer((parsed.hostname, parsed.port or 80), Handler)
    server.timeout = 1
    url = f"{ACCOUNTS}/authorize?" + urlencode({
        "client_id": require("CLIENT_ID"),
        "response_type": "code",
        "redirect_uri": redirect,
        "scope": " ".join(SCOPES),
        "state": state,
    })
    print(f"Opening the Spotify sign-in page. If it doesn't open, go to:\n{url}\n")
    webbrowser.open(url)
    deadline = time.time() + 300
    while not result and time.time() < deadline:
        server.handle_request()
    server.server_close()

    if not result:
        sys.exit("No reply from Spotify within 5 minutes.")
    if result.get("error"):
        sys.exit(f"Spotify sign-in failed: {result['error']}")
    if result.get("state") != state:
        sys.exit("Sign-in reply didn't match this request (state mismatch); try again.")

    tokens = token_request({"grant_type": "authorization_code", "code": result["code"],
                            "redirect_uri": redirect})
    save_env_value("SPOTIFY_REFRESH_TOKEN", tokens["refresh_token"])
    save_env_value("SPOTIFY_AUTHORIZED", datetime.now().strftime("%Y-%m-%d"))
    me = api_session(tokens["access_token"]).get(f"{API}/me", timeout=30).json()
    print(f"Signed in as {me.get('display_name') or me.get('id')}. Refresh token saved to .env.")


def api_session(access_token=None):
    if access_token is None:
        tokens = token_request({"grant_type": "refresh_token",
                                "refresh_token": require("SPOTIFY_REFRESH_TOKEN")})
        if tokens.get("refresh_token"):
            save_env_value("SPOTIFY_REFRESH_TOKEN", tokens["refresh_token"])
        access_token = tokens["access_token"]
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {access_token}"
    return session


class RateLimited(Exception):
    """Spotify wants a wait too long to sit through (e.g. the app's daily quota is used up)."""

    def __init__(self, seconds):
        super().__init__(f"Spotify's rate limit for this app is reached; try again in about "
                         f"{seconds / 3600:.1f} hours.")


def fetch(session, url, params=None, retries=4):
    """GET a Spotify API URL; returns the JSON, or None for 204 (nothing playing)."""
    for attempt in range(retries + 1):
        r = session.get(url, params=params, timeout=30)
        if r.status_code == 429 and attempt < retries:
            wait = int(r.headers.get("Retry-After", "5"))
            if wait > 120:
                raise RateLimited(wait)
            time.sleep(wait + 1)
            continue
        if r.status_code >= 500 and attempt < retries:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()


def pick_image(images, width):
    """The smallest image at least `width` px wide, else the largest; returns its URL or None."""
    images = sorted((i for i in images or [] if i.get("url")), key=lambda i: i.get("width") or 0)
    for image in images:
        if (image.get("width") or 0) >= width:
            return image["url"]
    return images[-1]["url"] if images else None


def load_art():
    return json.loads(ART.read_text(encoding="utf-8")) if ART.exists() else {}


def remember_art(art, item):
    """Add an album's or show's images (and an album's release year) to the art cache; True if it changed."""
    item = item or {}
    uri, images = item.get("uri"), item.get("images")
    if not uri or not images or (uri in art and "year" in art[uri]):
        return False
    entry = {"image": pick_image(images, 300), "image_large": pick_image(images, 640), "year": None}
    if (item.get("release_date") or "")[:4].isdigit():
        entry["year"] = int(item["release_date"][:4])
    art[uri] = entry
    return True


def slim_track(track):
    return {
        "uri": track["uri"],
        "name": track["name"],
        "artists": [a["name"] for a in track.get("artists", [])],
        "artist_uris": [a["uri"] for a in track.get("artists", [])],
        "album": (track.get("album") or {}).get("name"),
        "album_uri": (track.get("album") or {}).get("uri"),
        "duration_ms": track.get("duration_ms"),
    }


def sync_plays(session):
    seen = set()
    if PLAYS.exists():
        for line in PLAYS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["played_at"])
    data = fetch(session, f"{API}/me/player/recently-played", {"limit": 50}) or {}
    art = load_art()
    if any([remember_art(art, i["track"].get("album")) for i in data.get("items", [])]):
        write_atomic(ART, json.dumps(art, indent=2, ensure_ascii=False))
    new = []
    for item in data.get("items", []):
        if item["played_at"] in seen:
            continue
        context = item.get("context") or {}
        new.append({"played_at": item["played_at"], **slim_track(item["track"]),
                    "context": context.get("uri")})
    new.sort(key=lambda p: p["played_at"])
    if new:
        PLAYS.parent.mkdir(parents=True, exist_ok=True)
        with PLAYS.open("a", encoding="utf-8") as f:
            for play in new:
                f.write(json.dumps(play, ensure_ascii=False) + "\n")
    if seen and len(new) == len(data.get("items", [])) == 50:
        print("Warning: all 50 recent plays were new, so some plays since the last sync were probably missed.")
    return new


def sync_episode(session):
    now = fetch(session, f"{API}/me/player/currently-playing", {"additional_types": "episode"})
    if not now or now.get("currently_playing_type") != "episode" or not now.get("item"):
        return None
    ep = now["item"]
    art = load_art()
    show = ep.get("show") or {}
    if show.get("uri") not in art and remember_art(art, show):
        write_atomic(ART, json.dumps(art, indent=2, ensure_ascii=False))
    episode_art = pick_image(ep.get("images"), 300)
    episodes =json.loads(EPISODES.read_text(encoding="utf-8")) if EPISODES.exists() else {}
    entry = episodes.get(ep["uri"]) or {
        "name": ep["name"],
        "show": (ep.get("show") or {}).get("name"),
        "show_uri": (ep.get("show") or {}).get("uri"),
        "release_date": ep.get("release_date"),
        "duration_ms": ep.get("duration_ms"),
        "image": episode_art,
        "first_seen": now_iso(),
        "progress_ms": 0,
    }
    entry["last_seen"] = now_iso()
    entry["progress_ms"] = max(entry["progress_ms"], now.get("progress_ms") or 0)
    episodes[ep["uri"]] = entry
    write_atomic(EPISODES, json.dumps(episodes, indent=2, ensure_ascii=False))
    return entry


def sync_shows(session, per_run=8):
    """Catch episodes heard between syncs from each recent show's resume points (where Spotify would resume
    playback). Shows heard in the last week are checked every 2 hours, those heard in the last 30 days daily,
    at most `per_run` shows per sync so the catalog quota lasts. A newly found episode is dated now, except on
    a show's first check, when it gets its release date (it could have been heard any time before)."""
    now = datetime.now(timezone.utc)
    def ago(days):
        return (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    episodes = json.loads(EPISODES.read_text(encoding="utf-8")) if EPISODES.exists() else {}
    history = json.loads(SHOWS.read_text(encoding="utf-8")) if SHOWS.exists() else {}
    checks = json.loads(SHOW_CHECKS.read_text(encoding="utf-8")) if SHOW_CHECKS.exists() else {}
    last_heard = {}
    for show in history.values():
        if show.get("uri"):
            last_heard[show["uri"]] = max(last_heard.get(show["uri"], ""), show["last"])
    for e in episodes.values():
        if e.get("show_uri"):
            last_heard[e["show_uri"]] = max(last_heard.get(e["show_uri"], ""), e["last_seen"])
    due = [uri for uri, heard in last_heard.items() if heard >= ago(30)
           and checks.get(uri, "") < ago(1 / 12 if heard >= ago(7) else 1)]
    due.sort(key=lambda uri: checks.get(uri, ""))
    found = []
    try:
        for uri in due[:per_run]:
            page = try_fetch(session, f"{API}/shows/{uri.split(':')[-1]}/episodes", {"limit": 10}) or {}
            first_check = uri not in checks
            checks[uri] = now_iso()
            for ep in page.get("items") or []:
                resume = (ep or {}).get("resume_point") or {}
                progress = ep.get("duration_ms") if resume.get("fully_played") else resume.get("resume_position_ms")
                if not progress or progress < MIN_EPISODE_MS:
                    continue
                entry = episodes.get(ep["uri"])
                if entry and progress <= entry["progress_ms"]:
                    continue
                if not entry:
                    seen = f"{ep['release_date']}T12:00:00Z" if first_check and len(ep.get("release_date") or "") == 10 else now_iso()
                    entry = episodes[ep["uri"]] = {
                        "name": ep["name"],
                        "show": next((s["name"] for s in history.values() if s.get("uri") == uri), None)
                                or next((e["show"] for e in episodes.values() if e.get("show_uri") == uri), None),
                        "show_uri": uri,
                        "release_date": ep.get("release_date"),
                        "duration_ms": ep.get("duration_ms"),
                        "image": pick_image(ep.get("images"), 300),
                        "first_seen": min(seen, now_iso()),
                        "progress_ms": 0,
                    }
                    found.append(entry)
                    entry["last_seen"] = entry["first_seen"]
                elif not first_check:
                    entry["last_seen"] = now_iso()
                entry["progress_ms"] = progress
    except RateLimited as e:
        print(f"Show check stopped: {e}")
    write_atomic(EPISODES, json.dumps(episodes, indent=2, ensure_ascii=False))
    write_atomic(SHOW_CHECKS, json.dumps(checks, indent=2))
    return found


def sync():
    session = api_session()
    new = sync_plays(session)
    print(f"{len(new)} new play(s)" + (f", latest: {new[-1]['name']} - {', '.join(new[-1]['artists'])}" if new else ""))
    episode = sync_episode(session)
    if episode:
        print(f"Podcast playing: {episode['show']} - {episode['name']}")
    for found in sync_shows(session):
        print(f"Podcast heard: {found['show']} - {found['name']}")


def fill_art(delay=0.2):
    """Fetch cover art for saved albums missing from the art cache, one album at a time."""
    art = load_art()
    wanted = []
    if PLAYS.exists():
        for line in PLAYS.read_text(encoding="utf-8").splitlines():
            uri = json.loads(line).get("album_uri") if line.strip() else None
            if uri and "year" not in art.get(uri, {}) and uri not in wanted:
                wanted.append(uri)
    if not wanted:
        print("All saved albums have cover art.")
        return
    session = api_session()
    for n, uri in enumerate(wanted, 1):
        album = fetch(session, f"{API}/albums/{uri.split(':')[-1]}") or {}
        remember_art(art, album)
        if n % 25 == 0:
            write_atomic(ART, json.dumps(art, indent=2, ensure_ascii=False))
        time.sleep(delay)
    write_atomic(ART, json.dumps(art, indent=2, ensure_ascii=False))
    print(f"Cover art fetched for {len(wanted)} album(s).")


def try_fetch(session, url, params=None):
    """fetch, but None for a track or episode Spotify no longer has (404 and the like)."""
    try:
        return fetch(session, url, params)
    except requests.HTTPError:
        return None


def import_history(limit=None, delay=0.2):
    """Turn the privacy export in data/export/ into history.jsonl and podcast_history.json.

    The export has no album or artist URIs, so each track is looked up once (and each show once, from
    one of its episodes); lookups are saved as they go, so an interrupted import picks up where it left off.
    `limit` caps the lookups per run, so a scheduled import leaves the app's daily quota room for `sync`.
    When nothing is left to look up, data/import.done is written (sync.bat stops importing then)."""
    files = sorted(EXPORT.glob("Streaming_History_Audio_*.json"))
    if not files:
        sys.exit(f"No Streaming_History_Audio_*.json files in {EXPORT}; unzip the export there first.")
    streams = [s for f in files for s in json.loads(f.read_text(encoding="utf-8")) if s["ms_played"] >= MIN_PLAY_MS]
    streams.sort(key=lambda s: s["ts"])
    music = [s for s in streams if s.get("spotify_track_uri")]
    talk = [s for s in streams if s.get("spotify_episode_uri") and s.get("episode_show_name")]

    tracks = json.loads(TRACKS.read_text(encoding="utf-8")) if TRACKS.exists() else {}
    shows = json.loads(SHOWS.read_text(encoding="utf-8")) if SHOWS.exists() else {}
    # A show's uri is None until it's looked up, and False if the lookup found nothing.
    show_uris = {name: s["uri"] for name, s in shows.items() if s.get("uri") is not None}
    art = load_art()
    # One track per album is enough (the rest borrow its album and artists), most-played album first,
    # so a lookup cut short by the rate limit still covers most of the plays.
    def album_key(s):
        return s["master_metadata_album_artist_name"], s["master_metadata_album_album_name"]

    def album_meta():
        meta = {}
        for s in music:
            if tracks.get(s["spotify_track_uri"]):
                meta.setdefault(album_key(s), tracks[s["spotify_track_uri"]])
        return meta

    known = album_meta()
    album_plays, album_tracks = Counter(), {}
    for s in music:
        album_plays[album_key(s)] += 1
        album_tracks.setdefault(album_key(s), Counter())[s["spotify_track_uri"]] += 1
    wanted = []
    for key, _ in album_plays.most_common():
        if key not in known:
            untried = [uri for uri, _ in album_tracks[key].most_common() if uri not in tracks]
            wanted += untried[:1]
    shows_by_time = Counter()
    latest_episode = {}
    for s in talk:
        shows_by_time[s["episode_show_name"]] += s["ms_played"]
        latest_episode[s["episode_show_name"]] = s["spotify_episode_uri"]
    wanted_shows = [name for name, _ in shows_by_time.most_common() if name not in show_uris]
    finished = len(wanted) + len(wanted_shows) <= (limit if limit is not None else float("inf"))
    if limit is not None:  # shows first: a dozen are on the site, while the albums left are rarely played
        wanted_shows = wanted_shows[:limit]
        wanted = wanted[:limit - len(wanted_shows)]
    session = api_session() if wanted or wanted_shows else None

    def save():
        write_atomic(TRACKS, json.dumps(tracks, ensure_ascii=False))
        write_atomic(ART, json.dumps(art, indent=2, ensure_ascii=False))

    try:
        for name in wanted_shows:
            episode = try_fetch(session, f"{API}/episodes/{latest_episode[name].split(':')[-1]}") or {}
            show_uris[name] = (episode.get("show") or {}).get("uri") or False
            remember_art(art, episode.get("show"))
            time.sleep(delay)
        for n, uri in enumerate(wanted, 1):
            track = try_fetch(session, f"{API}/tracks/{uri.split(':')[-1]}")
            tracks[uri] = slim_track(track) if track else None
            if track:
                remember_art(art, track.get("album"))
            else:
                finished = False  # its album is tried again next run with another of its tracks
            if n % 100 == 0:
                save()
                print(f"Looked up {n} of {len(wanted)} tracks", flush=True)
            time.sleep(delay)
    except RateLimited as e:
        finished = False
        print(f"{e} Saving what's looked up so far; run `import` again then to look up the rest.")
    save()

    known = album_meta()
    lines = []
    for s in music:
        uri = s["spotify_track_uri"]
        play = {"uri": uri, "name": s["master_metadata_track_name"],
                "artists": [s["master_metadata_album_artist_name"] or "Unknown"], "artist_uris": [],
                "album": s["master_metadata_album_album_name"], "album_uri": None, "duration_ms": None}
        if tracks.get(uri):
            play.update(tracks[uri])
        elif album_key(s) in known:
            sibling = known[album_key(s)]
            play.update({k: sibling[k] for k in ("artists", "artist_uris", "album", "album_uri")})
        lines.append(json.dumps({"played_at": s["ts"], **play, "uri": uri, "ms_played": s["ms_played"]},
                                ensure_ascii=False))
    write_atomic(HISTORY, "\n".join(lines) + "\n")

    shows = {}
    for s in talk:
        name = s["episode_show_name"]
        show = shows.setdefault(name, {"name": name, "uri": show_uris.get(name), "plays": 0, "ms_played": 0,
                                       "first": s["ts"]})
        show["plays"] += 1
        show["ms_played"] += s["ms_played"]
        show["last"] = s["ts"]
    write_atomic(SHOWS, json.dumps(shows, indent=2, ensure_ascii=False))
    missing = sum(1 for key in album_plays if key not in known)
    covered = sum(n for key, n in album_plays.items() if key in known)
    print(f"Imported {len(music)} music plays ({music[0]['ts'][:10]} to {music[-1]['ts'][:10]}) and "
          f"{len(talk)} podcast plays from {len(shows)} shows. Album details for "
          f"{len(album_plays) - missing} of {len(album_plays)} albums ({covered / len(music):.0%} of plays)"
          + (f"; {missing} still to look up." if missing else "."))
    if finished:
        IMPORT_DONE.write_text(now_iso() + "\n", encoding="utf-8")
        print("All lookups done.")


def open_url(uri):
    """spotify:album:abc -> https://open.spotify.com/album/abc"""
    return "https://open.spotify.com/" + "/".join(uri.split(":")[1:]) if uri else None


def load_plays():
    """All music plays, oldest first: the export's history, then live plays after its last one."""
    def read_plays(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()] if path.exists() else []

    plays = read_plays(HISTORY)
    exported_to = plays[-1]["played_at"][:19] if plays else ""
    return plays + [p for p in read_plays(PLAYS) if p["played_at"][:19] > exported_to]


def build():
    """Write docs/data/listening.js (a script, so docs/index.html also works opened as a file)."""
    art = load_art()
    plays = load_plays()
    episodes = json.loads(EPISODES.read_text(encoding="utf-8")) if EPISODES.exists() else {}
    shows = json.loads(SHOWS.read_text(encoding="utf-8")) if SHOWS.exists() else {}

    def record(p):
        a = art.get(p.get("album_uri"), {})
        return {"name": p["name"], "artist": ", ".join(p["artists"]), "album": p.get("album"),
                "year": a.get("year"), "image": a.get("image"), "image_large": a.get("image_large"),
                "url": open_url(p["uri"]), "album_url": open_url(p.get("album_uri"))}

    recent = []
    for p in reversed(plays):
        if recent and recent[-1]["url"] == open_url(p["uri"]):
            continue
        recent.append({**record(p), "played_at": p["played_at"]})
        if len(recent) == 48:
            break

    artists, albums, decades = {}, {}, {}
    for p in plays:
        name = p["artists"][0] if p["artists"] else "Unknown"
        a = artists.setdefault(name, {"name": name, "plays": 0, "albums": {},
                                      "url": open_url((p.get("artist_uris") or [None])[0])})
        a["plays"] += 1
        a["albums"][p.get("album_uri")] = a["albums"].get(p.get("album_uri"), 0) + 1
        a["url"] = a["url"] or open_url((p.get("artist_uris") or [None])[0])
        if p.get("album_uri"):
            al = albums.setdefault(p["album_uri"], {**record(p), "artist": name, "plays": 0})
            al["plays"] += 1
        year = art.get(p.get("album_uri"), {}).get("year")
        if year:
            d = decades.setdefault(year // 10 * 10, {"decade": year // 10 * 10, "plays": 0, "albums": {}})
            d["plays"] += 1
            d["albums"][p["album_uri"]] = d["albums"].get(p["album_uri"], 0) + 1

    top_artists = sorted(artists.values(), key=lambda a: -a["plays"])[:10]
    for a in top_artists:
        best = max(a.pop("albums").items(), key=lambda kv: kv[1])[0]
        a["image"] = art.get(best, {}).get("image")
    for d in decades.values():
        ranked = sorted(d.pop("albums").items(), key=lambda kv: -kv[1])
        d["covers"] = [{**albums[uri], "plays": n} for uri, n in ranked[:8]]

    site = {
        "title": os.environ.get("SITE_TITLE") or "The Listening Booth",
        "generated": now_iso(),
        "totals": {
            "plays": len(plays),
            "hours": round(sum(p.get("ms_played") or p.get("duration_ms") or 0 for p in plays) / 3_600_000, 1),
            "artists": len(artists),
            "albums": len(albums),
            "first": plays[0]["played_at"] if plays else None,
        },
        "recent": recent,
        "top_artists": top_artists,
        "top_albums": sorted(albums.values(), key=lambda a: -a["plays"])[:12],
        "decades": sorted(decades.values(), key=lambda d: d["decade"]),
        "episodes": sorted(({**e, "url": open_url(uri), "show_image": art.get(e.get("show_uri"), {}).get("image")}
                            for uri, e in episodes.items() if e["progress_ms"] >= MIN_EPISODE_MS),
                           key=lambda e: e["last_seen"], reverse=True)[:12],
        "top_shows": [{"name": s["name"], "plays": s["plays"], "hours": round(s["ms_played"] / 3_600_000),
                       "image": art.get(s["uri"], {}).get("image"), "url": open_url(s["uri"])}
                      for s in sorted(shows.values(), key=lambda s: -s["ms_played"])[:12]],
    }
    out = ROOT / "docs" / "data" / "listening.js"
    write_atomic(out, "window.LISTENING = " + json.dumps(site, ensure_ascii=False, indent=1) + ";\n")
    print(f"Wrote {out.relative_to(ROOT)}: {len(plays)} plays, {len(artists)} artists, {len(albums)} albums.")


def wrapped(years=None):
    """Write docs/data/wrapped/<year>.js: a year in review for docs/wrapped.html?year=<year>.

    Years are local calendar years (Wrapped itself stops around the end of October). Podcasts come
    from the export only, since live sync sees episodes just when one happens to be playing."""
    art = load_art()
    shows_cache = json.loads(SHOWS.read_text(encoding="utf-8")) if SHOWS.exists() else {}
    first_year = {}  # artist -> first year heard, for "discoveries"
    by_year = {}
    for p in load_plays():
        p["local"] = datetime.fromisoformat(p["played_at"].replace("Z", "+00:00")).astimezone()
        name = p["artists"][0] if p["artists"] else "Unknown"
        first_year.setdefault(name, p["local"].year)
        by_year.setdefault(p["local"].year, []).append(p)
    episodes_by_year = {}
    for f in sorted(EXPORT.glob("Streaming_History_Audio_*.json")):
        for s in json.loads(f.read_text(encoding="utf-8")):
            if s.get("spotify_episode_uri") and s.get("episode_show_name") and s["ms_played"] >= MIN_PLAY_MS:
                local = datetime.fromisoformat(s["ts"].replace("Z", "+00:00")).astimezone()
                episodes_by_year.setdefault(local.year, []).append(s)
    years = years or sorted(by_year)
    out_dir = ROOT / "docs" / "data" / "wrapped"

    def ms(p):
        return p.get("ms_played") or p.get("duration_ms") or 0

    def minutes(n):
        return round(n / 60_000)

    def track_card(p, **extra):
        a = art.get(p.get("album_uri"), {})
        return {"name": p["name"], "artist": ", ".join(p["artists"]), "album": p.get("album"),
                "image": a.get("image"), "image_large": a.get("image_large"), "year": a.get("year"),
                "url": open_url(p["uri"]), **extra}

    for year in years:
        plays = by_year.get(year, [])
        if not plays:
            print(f"No plays in {year}.")
            continue
        artists, tracks, albums = {}, {}, {}
        months, hours, weekdays = [0] * 12, [0] * 24, [0] * 7
        days, day_track, decades = Counter(), Counter(), Counter()
        for p in plays:
            t, d = ms(p), p["local"]
            name = p["artists"][0] if p["artists"] else "Unknown"
            a = artists.setdefault(name, {"name": name, "ms": 0, "plays": 0, "albums": Counter(),
                                          "first": p["played_at"], "url": None})
            a["ms"] += t
            a["plays"] += 1
            a["albums"][p.get("album_uri")] += t
            a["url"] = a["url"] or open_url((p.get("artist_uris") or [None])[0])
            tr = tracks.setdefault(p["uri"], {"p": p, "ms": 0, "plays": 0, "first": p["played_at"]})
            tr["ms"] += t
            tr["plays"] += 1
            if p.get("album_uri"):
                al = albums.setdefault(p["album_uri"], {"p": p, "artist": name, "ms": 0, "plays": 0})
                al["ms"] += t
                al["plays"] += 1
                if art.get(p["album_uri"], {}).get("year"):
                    decades[art[p["album_uri"]]["year"] // 10 * 10] += t
            months[d.month - 1] += t
            hours[d.hour] += t
            weekdays[d.weekday()] += t
            days[d.date().isoformat()] += t
            day_track[(d.date().isoformat(), p["uri"])] += 1

        total = sum(ms(p) for p in plays)
        ranked_artists = sorted(artists.values(), key=lambda a: -a["ms"])
        for a in ranked_artists:  # an artist's picture is the cover of their most-played album
            a["image"] = next((art[uri].get("image_large") for uri, _ in a.pop("albums").most_common()
                               if art.get(uri, {}).get("image_large")), None)
        top_tracks = sorted(tracks.values(), key=lambda t: (-t["plays"], -t["ms"]))[:10]
        top_albums = sorted(albums.values(), key=lambda a: -a["ms"])[:10]
        best_day, best_day_ms = days.most_common(1)[0]
        best_day_plays = [p for p in plays if p["local"].date().isoformat() == best_day]
        (repeat_day, repeat_uri), repeat_n = day_track.most_common(1)[0]

        listened = sorted(days)
        streak = best = 1
        streak_end = listened[0]
        for prev, cur in zip(listened, listened[1:]):
            streak = streak + 1 if (datetime.fromisoformat(cur) - datetime.fromisoformat(prev)).days == 1 else 1
            if streak > best:
                best, streak_end = streak, cur

        new_artists = [a for a in ranked_artists if first_year.get(a["name"]) == year]
        prev = by_year.get(year - 1, [])
        prev_top = Counter()
        for p in prev:
            prev_top[p["artists"][0] if p["artists"] else "Unknown"] += ms(p)

        shows = {}
        for s in episodes_by_year.get(year, []):
            sh = shows.setdefault(s["episode_show_name"], {"name": s["episode_show_name"], "ms": 0, "episodes": set()})
            sh["ms"] += s["ms_played"]
            sh["episodes"].add(s["spotify_episode_uri"])
        top_shows = sorted(shows.values(), key=lambda s: -s["ms"])[:5]

        data = {
            "year": year,
            "generated": now_iso(),
            "through": plays[-1]["played_at"],
            "minutes": minutes(total),
            "plays": len(plays),
            "artists_count": len(artists),
            "tracks_count": len(tracks),
            "albums_count": len(albums),
            "days_listened": len(days),
            "longest_streak": {"days": best, "ended": streak_end},
            "previous_minutes": minutes(sum(ms(p) for p in prev)) if prev else None,
            "months": [minutes(m) for m in months],
            "hours": [minutes(h) for h in hours],
            "weekdays": [minutes(w) for w in weekdays],
            "days": {day: minutes(n) for day, n in sorted(days.items())},
            "top_artists": [{"name": a["name"], "minutes": minutes(a["ms"]), "plays": a["plays"],
                             "share": round(a["ms"] / total * 100, 1), "first": a["first"],
                             "image": a.get("image"), "url": a["url"],
                             "last_year_rank": next((i + 1 for i, (n, _) in enumerate(prev_top.most_common())
                                                     if n == a["name"]), None)}
                            for a in ranked_artists[:10]],
            "top_tracks": [track_card(t["p"], plays=t["plays"], minutes=minutes(t["ms"]), first=t["first"])
                           for t in top_tracks],
            "top_albums": [{**track_card(a["p"]), "name": a["p"].get("album"), "artist": a["artist"],
                            "url": open_url(a["p"]["album_uri"]), "plays": a["plays"], "minutes": minutes(a["ms"])}
                           for a in top_albums],
            "best_day": {"date": best_day, "minutes": minutes(best_day_ms), "plays": len(best_day_plays),
                         "artists": [n for n, _ in Counter(p["artists"][0] for p in best_day_plays
                                                            if p["artists"]).most_common(3)]},
            "on_repeat": {"date": repeat_day, "plays": repeat_n, **track_card(tracks[repeat_uri]["p"])},
            "first_song": track_card(plays[0], played_at=plays[0]["played_at"]),
            "decades": [{"decade": d, "minutes": minutes(n)} for d, n in sorted(decades.items())],
            "discoveries": {"count": len(new_artists),
                            "top": [{"name": a["name"], "minutes": minutes(a["ms"]), "image": a.get("image"),
                                     "url": a["url"]} for a in new_artists[:5]]},
            "podcasts": {"minutes": minutes(sum(s["ms"] for s in shows.values())),
                         "shows_count": len(shows),
                         "top": [{"name": s["name"], "minutes": minutes(s["ms"]), "episodes": len(s["episodes"]),
                                  "image": art.get((shows_cache.get(s["name"]) or {}).get("uri"), {}).get("image"),
                                  "url": open_url((shows_cache.get(s["name"]) or {}).get("uri"))}
                                 for s in top_shows]},
        }
        out = out_dir / f"{year}.js"
        write_atomic(out, f"(window.WRAPPED = window.WRAPPED || {{}})[{year}] = "
                          + json.dumps(data, ensure_ascii=False) + ";\n")
        print(f"Wrote {out.relative_to(ROOT)}: {len(plays)} plays, {minutes(total):,} minutes.")
    available = sorted(int(f.stem) for f in out_dir.glob("*.js") if f.stem.isdigit())
    write_atomic(out_dir / "index.js", f"window.WRAPPED_YEARS = {json.dumps(available)};\n")


def git(*args):
    """Run git in the repo; never waits for a sign-in prompt (with nobody there, it fails instead)."""
    try:
        proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never"))
        return proc.returncode == 0, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def site_url():
    """The GitHub Pages address for the origin remote (github.com/owner/repo -> owner.github.io/repo/)."""
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"]
    ok, url = git("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(\.git)?$", url) if ok else None
    return f"https://{m.group(1).lower()}.github.io/{m.group(2)}/" if m else None


def publish():
    """Commit and push docs/data/ for GitHub Pages when it really changed (not just the build timestamp).

    Only docs/data/ is committed, so code being edited stays out of it. AUTO_PUBLISH=off in .env turns
    this off."""
    if os.environ.get("AUTO_PUBLISH", "on").lower() in ("off", "no", "false", "0"):
        return
    ok, out = git("add", "docs/data")
    if ok:
        unchanged, _ = git("diff", "--cached", "--quiet", "-I", r'^\s*"generated":')
        if unchanged:
            git("reset", "-q", "docs/data")
            return
        plays = load_plays()
        last = datetime.fromisoformat(plays[-1]["played_at"].replace("Z", "+00:00")).astimezone() if plays else None
        ok, out = git("commit", "-q", "-m", f"Listening data through {last:%b} {last.day}, {last:%I:%M %p}"
                      if last else "Update listening data")
    if ok:
        ok, out = git("pull", "-q", "--rebase", "--autostash")
        if not ok:
            git("rebase", "--abort")
    if ok:
        ok, out = git("push", "-q")
    print("Published to GitHub." if ok else
          f"Publish failed: {(out.splitlines() or ['git error'])[-1]} (the commit is kept; it goes out next run)")


def read_log(since):
    """Scheduled runs in data/sync.log since `since`, as (start time, lines) pairs."""
    runs = []
    if not SYNC_LOG.exists():
        return runs
    for line in SYNC_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("=== "):  # "=== Fri 10/30/2026 21:40:01.53", written by sync.bat
            try:
                started = datetime.strptime(line[4:].split(" ", 1)[1].strip(), "%m/%d/%Y %H:%M:%S.%f")
            except (ValueError, IndexError):
                started = None
            runs.append((started, []))
        elif runs:
            runs[-1][1].append(line)
    return [(t, lines) for t, lines in runs if t and t.astimezone() >= since]


def report(force=False, dry_run=False):
    """Email a daily summary: the last 24 hours of scheduled runs, listening, and import progress.

    sync.bat calls this every run; it sends once a day, on the first run from REPORT_HOUR (default 8).
    Needs SMTP_USER and SMTP_PASSWORD in .env (for Gmail, an app password); REPORT_TO defaults to SMTP_USER."""
    now = datetime.now().astimezone()
    today = now.date().isoformat()
    sent = REPORT_SENT.read_text(encoding="utf-8").strip() if REPORT_SENT.exists() else ""
    if not (force or dry_run) and (now.hour < int(os.environ.get("REPORT_HOUR", "8")) or sent == today):
        return
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if not dry_run and not (user and password):
        if force:
            sys.exit("SMTP_USER and SMTP_PASSWORD are missing from .env")
        return
    r = report_data(now)
    text, html = report_text(r), report_html(r)

    if dry_run:
        preview = DATA / "report-preview.html"
        write_atomic(preview, html)
        print(f"Subject: {r['subject']}\n\n{text}\n\nHTML version: {preview}")
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = r["subject"], f"The Listening Booth <{user}>", os.environ.get("REPORT_TO") or user
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(os.environ.get("SMTP_HOST") or "smtp.gmail.com", int(os.environ.get("SMTP_PORT") or 587),
                      timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password.replace(" ", ""))
        smtp.send_message(msg)
    REPORT_SENT.write_text(today + "\n", encoding="utf-8")
    print(f"Sent the daily report to {msg['To']}.")


def clean_title(name):
    """'Black Dog - Remaster', 'Time Passages - 2004 Remastered Version' -> the song's name."""
    name = re.sub(r"\s+-\s+(\d{4}\s+)?(digital\s+)?remaster(ed)?(\s+\d{4})?(\s+version)?$", "", name or "", flags=re.I)
    return re.sub(r"\s+\((\d{4}\s+)?remaster(ed)?(\s+\d{4})?(\s+version)?\)$", "", name, flags=re.I)


def report_data(now):
    """Everything the daily report says, for report_text and report_html."""
    since = now - timedelta(days=1)
    art = load_art()
    runs = read_log(since)
    log = [line for _, lines in runs for line in lines]
    all_plays = load_plays()
    for p in all_plays:
        p["local"] = datetime.fromisoformat(p["played_at"].replace("Z", "+00:00")).astimezone()
    plays = [p for p in all_plays if p["local"] >= since]

    def cover(p, size="image"):
        return art.get(p.get("album_uri"), {}).get(size)

    tracks = {}
    for p in plays:
        t = tracks.setdefault(p["uri"], {"name": clean_title(p["name"]), "artist": ", ".join(p["artists"]), "album": p.get("album"),
                                         "year": art.get(p.get("album_uri"), {}).get("year"), "plays": 0,
                                         "image": cover(p), "image_large": cover(p, "image_large"),
                                         "url": open_url(p["uri"])})
        t["plays"] += 1
    artists = {}
    for p in plays:
        name = p["artists"][0] if p["artists"] else "Unknown"
        a = artists.setdefault(name, {"name": name, "plays": 0, "image": cover(p),
                                      "url": open_url((p.get("artist_uris") or [None])[0])})
        a["plays"] += 1
    episodes = json.loads(EPISODES.read_text(encoding="utf-8")) if EPISODES.exists() else {}
    podcasts = sorted(({**e, "url": open_url(uri),
                        "image": e.get("image") or art.get(e.get("show_uri"), {}).get("image"),
                        "pct": min(100, round(e["progress_ms"] / e["duration_ms"] * 100)) if e.get("duration_ms") else None}
                       for uri, e in episodes.items()
                       if e["progress_ms"] >= MIN_EPISODE_MS and datetime.fromisoformat(e["last_seen"].replace("Z", "+00:00")) >= since),
                      key=lambda e: e["last_seen"], reverse=True)

    # On this date in earlier years: the most-played song of each of the last three years that has plays.
    this_day = {}
    for p in all_plays:
        if (p["local"].month, p["local"].day) == (now.month, now.day) and p["local"].year < now.year:
            this_day.setdefault(p["local"].year, []).append(p)
    on_this_day = []
    for year in sorted(this_day, reverse=True)[:3]:
        day_plays = this_day[year]
        uri, n = Counter(p["uri"] for p in day_plays).most_common(1)[0]
        p = next(p for p in day_plays if p["uri"] == uri)
        on_this_day.append({"year": year, "plays": len(day_plays), "top": clean_title(p["name"]), "artist": ", ".join(p["artists"]),
                            "top_plays": n, "image": cover(p), "url": open_url(uri)})

    problems = list(dict.fromkeys(line for line in log if re.search(r"Traceback|Error|error:|Warning|failed", line)))
    published = sum(1 for line in log if line.startswith("Published to GitHub"))
    expected = 48
    status = "bad" if any("Traceback" in l or "rror" in l for l in problems) else \
             "warn" if problems or len(runs) < expected - 4 else "ok"

    album_line = next((l for l in reversed(log) if "Album details" in l), "")
    m = re.search(r"Album details for (\d+) of (\d+) albums \((\d+)% of plays\)", album_line)
    shows = json.loads(SHOWS.read_text(encoding="utf-8")).values() if SHOWS.exists() else []
    show_counts = Counter("pending" if s.get("uri") is None else "failed" if s["uri"] is False else "done" for s in shows)
    imported = None if IMPORT_DONE.exists() else {
        "albums": (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None,
        "shows": (show_counts["done"], show_counts["failed"], sum(show_counts.values())),
        "rate_limited": any("rate limit" in l for l in log)}

    top_tracks = sorted(tracks.values(), key=lambda t: -t["plays"])[:5]
    minutes = round(sum(p.get("ms_played") or p.get("duration_ms") or 0 for p in plays) / 60_000)
    if status == "bad":
        subject = "The Listening Booth: the scheduled sync hit an error"
    elif top_tracks:
        subject = f"The Listening Booth: {len(plays)} plays · {top_tracks[0]['name']} on top"
    else:
        subject = "The Listening Booth: a quiet day"
    if len(runs) < expected - 4:
        subject += f" · only {len(runs)} of {expected} runs"
    return {"now": now, "subject": subject, "status": status, "runs": len(runs), "expected": expected,
            "last_run": runs[-1][0] if runs else None, "problems": problems[:12], "plays": len(plays),
            "minutes": minutes, "artist_count": len(artists), "podcasts": podcasts[:6],
            "top_tracks": top_tracks, "top_artists": sorted(artists.values(), key=lambda a: -a["plays"])[:5],
            "on_this_day": on_this_day, "import": imported, "published": published, "site": site_url(),
            "totals": {"plays": len(all_plays), "since": all_plays[0]["local"].year if all_plays else None}}


def report_text(r):
    now = r["now"]
    out = [f"The Listening Booth - daily report, {now:%A, %B} {now.day}, {now.year}", "",
           {"ok": "All systems playing.", "warn": "Something needs a look.", "bad": "The scheduled sync hit an error."}[r["status"]],
           f"Scheduled runs in the last 24 hours: {r['runs']} of {r['expected']}"
           + (f", last at {r['last_run']:%I:%M %p}".replace(" 0", " ") if r["last_run"] else " - is the task still enabled?"),
           f"Published to the site {r['published']} time{'s' if r['published'] != 1 else ''}"
           + (f": {r['site']}" if r["site"] else "")]
    if r["problems"]:
        out += ["", "From the log:"] + [f"  {l}" for l in r["problems"]]
    out += ["", f"Last 24 hours: {r['plays']} plays, {r['minutes']} minutes, {r['artist_count']} artists"]
    if r["top_tracks"]:
        out += ["", "Top songs:"] + [f"  {i}. {t['name']} - {t['artist']} ({t['plays']}x)" for i, t in enumerate(r["top_tracks"], 1)]
        out += ["", "Top artists:"] + [f"  {i}. {a['name']} ({a['plays']})" for i, a in enumerate(r["top_artists"], 1)]
    if r["podcasts"]:
        out += ["", "Podcasts:"] + [f"  {e['show']}: {e['name']}" + (f" ({e['pct']}%)" if e["pct"] is not None else "")
                                    for e in r["podcasts"]]
    if r["on_this_day"]:
        out += ["", f"On {now:%B} {now.day} in other years:"] + [
            f"  {d['year']}: {d['plays']} plays, top song {d['top']} - {d['artist']}" for d in r["on_this_day"]]
    if r["import"]:
        i = r["import"]
        out += ["", "Streaming-history import:"]
        if i["albums"]:
            out.append(f"  Albums: {i['albums'][0]} of {i['albums'][1]} looked up ({i['albums'][2]}% of plays covered)")
        out.append(f"  Podcast shows: {i['shows'][0]} of {i['shows'][2]} looked up, {i['shows'][1]} not on Spotify")
        if i["rate_limited"]:
            out.append("  Spotify's daily lookup quota ran out at least once (expected; it resumes on its own).")
    out += ["", "Full log: data/sync.log"]
    return "\n".join(out)


# Email-safe styling, after GoneWalking's report: tables for layout, inline styles, a fixed light scheme.
INK, MUTED, LINE, PAGE, NIGHT, GREEN = "#1d1b22", "#6b6775", "#e4e1ea", "#efedf3", "#17141f", "#1a7f4b"
FONT = "font-family:'Segoe UI',-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;"
STATUS = {"ok": ("#1a7f37", "#dafbe1", "All systems playing"),
          "warn": ("#9a6700", "#fff8c5", "Something needs a look"),
          "bad": ("#cf222e", "#ffebe9", "The scheduled sync hit an error")}


def report_html(r):
    from html import escape as h
    now = r["now"]

    def section(title, inner):
        return (f'<tr><td class="px" style="padding:26px 28px 0;"><div style="{FONT}font-size:12px;font-weight:700;'
                f'color:{MUTED};text-transform:uppercase;letter-spacing:.08em;margin:0 0 12px;">{h(title)}</div>{inner}</td></tr>')

    def thumb(src, size=44):
        if not src:
            return (f'<div style="width:{size}px;height:{size}px;border-radius:4px;background:{LINE};'
                    f'text-align:center;line-height:{size}px;color:{MUTED};font-size:18px;">&#9835;</div>')
        return (f'<img src="{h(src)}" width="{size}" height="{size}" alt="" '
                f'style="display:block;width:{size}px;height:{size}px;border-radius:4px;border:0;">')

    def link(url, text, color=INK):
        return f'<a href="{h(url)}" style="color:{color};text-decoration:none;">{h(text)}</a>' if url else h(text)

    def rows(items):
        """A list of (picture, title, subtitle, value) rows."""
        return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
                + "".join(f'<tr><td width="56" style="padding:6px 0;border-bottom:1px solid {LINE};">{pic}</td>'
                          f'<td style="padding:6px 8px;border-bottom:1px solid {LINE};{FONT}">'
                          f'<div style="font-size:15px;font-weight:600;color:{INK};line-height:1.3;">{title}</div>'
                          f'<div style="font-size:13px;color:{MUTED};line-height:1.35;">{sub}</div></td>'
                          f'<td align="right" style="padding:6px 0;border-bottom:1px solid {LINE};{FONT}font-size:13px;'
                          f'color:{MUTED};white-space:nowrap;">{value}</td></tr>' for pic, title, sub, value in items)
                + "</table>")

    def bar(done, total, label):
        pct = 0 if not total else max(0, min(100, round(done / total * 100)))
        return (f'<div style="{FONT}font-size:14px;color:{INK};margin:0 0 6px;">{label}</div>'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 14px;">'
                f'<tr><td width="{pct}%" style="background:{GREEN};height:8px;border-radius:4px 0 0 4px;font-size:0;'
                f'line-height:0;">&nbsp;</td><td style="background:{LINE};height:8px;font-size:0;line-height:0;'
                f'border-radius:0 4px 4px 0;">&nbsp;</td></tr></table>')

    color, tint, headline = STATUS[r["status"]]
    runs = (f'{r["runs"]} of {r["expected"]} scheduled runs in the last day'
            + (f' · last at {r["last_run"]:%I:%M %p}'.replace(" 0", " ") if r["last_run"] else " · is the task still enabled?")
            + f' · published {r["published"]} time{"s" if r["published"] != 1 else ""}')
    body = [
        # Header: a dark "stage" band with the title and date.
        f'<tr><td class="px" style="background:{NIGHT};padding:26px 28px;border-radius:12px 12px 0 0;{FONT}">'
        f'<div style="font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#a9a3b8;">&#9679;&nbsp; Daily report</div>'
        f'<div style="font-size:26px;font-weight:800;color:#ffffff;margin-top:6px;letter-spacing:-.01em;">The Listening Booth</div>'
        f'<div style="font-size:13px;color:#a9a3b8;margin-top:4px;">{now:%A, %B} {now.day}, {now.year}</div>'
        + (f'<div style="margin-top:14px;"><a href="{h(r["site"])}" style="{FONT}display:inline-block;padding:7px 14px;'
           f'border:1px solid #4a4458;border-radius:999px;color:#ffffff;font-size:13px;font-weight:600;'
           f'text-decoration:none;">Open the dashboard &rarr;</a>&nbsp; <a href="{h(r["site"])}wrapped.html" '
           f'style="{FONT}display:inline-block;padding:7px 14px;border:1px solid #4a4458;border-radius:999px;'
           f'color:#ffffff;font-size:13px;font-weight:600;text-decoration:none;">Year in review &rarr;</a></div>'
           if r["site"] else "") + '</td></tr>',
        f'<tr><td class="px" style="padding:20px 28px 0;"><div style="{FONT}background:{tint};border-left:4px solid {color};'
        f'border-radius:6px;padding:12px 16px;"><div style="font-size:16px;font-weight:700;color:{color};">{h(headline)}</div>'
        f'<div style="font-size:14px;color:{INK};margin-top:2px;">{h(runs)}</div></div></td></tr>']
    if r["problems"]:
        body.append(section("From the log", f'<div style="background:{NIGHT};border-radius:8px;padding:12px 16px;'
                            f'font-family:Consolas,Menlo,monospace;font-size:12px;line-height:1.6;color:#e6e3ee;'
                            f'white-space:pre-wrap;word-break:break-word;">' + "<br>".join(h(l) for l in r["problems"]) + "</div>"))

    tiles = [("Plays", f'{r["plays"]:,}'), ("Minutes", f'{r["minutes"]:,}'), ("Artists", f'{r["artist_count"]:,}'),
             ("Podcasts", f'{len(r["podcasts"]):,}')]
    body.append(section("The last 24 hours",
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {LINE};'
        f'border-radius:10px;border-collapse:separate;"><tr>'
        + "".join(f'<td class="tile" width="25%" style="padding:12px 4px;text-align:center;{FONT}'
                  f'{"border-left:1px solid " + LINE + ";" if i else ""}"><div style="font-size:12px;color:{MUTED};">{k}</div>'
                  f'<div class="tile-value" style="font-size:22px;font-weight:800;color:{INK};margin-top:2px;">{v}</div></td>'
                  for i, (k, v) in enumerate(tiles))
        + "</tr></table>"))

    if r["top_tracks"]:
        t = r["top_tracks"][0]
        art = (f'<a href="{h(t["url"])}"><img src="{h(t["image_large"])}" width="542" alt="{h(t["album"] or t["name"])} cover" '
               f'style="display:block;width:100%;max-width:542px;height:auto;border:0;border-radius:10px 10px 0 0;"></a>'
               if t["image_large"] else "")
        meta = " · ".join(x for x in (t["album"], str(t["year"]) if t["year"] else "") if x)
        body.append(section("Song of the day",
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {LINE};'
            f'border-radius:10px;border-collapse:separate;"><tr><td>{art}</td></tr>'
            f'<tr><td style="background:{NIGHT};padding:16px 18px;border-radius:{"0 0" if art else "10px 10px"} 10px 10px;{FONT}">'
            f'<div style="font-size:12px;color:#c4f04a;font-weight:700;letter-spacing:.08em;text-transform:uppercase;">'
            f'Played {t["plays"]}&times;</div>'
            f'<div style="font-size:20px;font-weight:800;color:#ffffff;margin-top:4px;line-height:1.25;">{h(t["name"])}</div>'
            f'<div style="font-size:14px;color:#d6d2df;margin-top:3px;">{h(t["artist"])}</div>'
            + (f'<div style="font-size:13px;color:#a9a3b8;margin-top:2px;">{h(meta)}</div>' if meta else "")
            + f'<div style="margin-top:14px;"><a href="{h(t["url"])}" style="{FONT}background:{GREEN};color:#ffffff;'
              f'display:inline-block;padding:9px 16px;border-radius:999px;font-size:14px;font-weight:700;'
              f'text-decoration:none;">&#9654;&nbsp; Play on Spotify</a></div></td></tr></table>'))
        if len(r["top_tracks"]) > 1:
            body.append(section("Also on repeat", rows(
                (thumb(t["image"]), link(t["url"], t["name"]), h(t["artist"]), f'{t["plays"]}&times;')
                for t in r["top_tracks"][1:])))
        body.append(section("Top artists", rows(
            (thumb(a["image"]), link(a["url"], a["name"]), "", f'{a["plays"]} play{"s" if a["plays"] != 1 else ""}')
            for a in r["top_artists"])))
    if r["podcasts"]:
        body.append(section("Podcasts", rows(
            (thumb(e["image"]), link(e["url"], e["name"]), h(e["show"]), f'{e["pct"]}%' if e["pct"] is not None else "")
            for e in r["podcasts"])))
    if r["on_this_day"]:
        body.append(section(f"On {now:%B} {now.day} in other years", rows(
            (thumb(d["image"]), f'{d["year"]} · ' + link(d["url"], d["top"]), h(d["artist"]),
             f'{d["plays"]} play{"s" if d["plays"] != 1 else ""} that day') for d in r["on_this_day"])))
    if r["import"]:
        i = r["import"]
        inner = ""
        if i["albums"]:
            inner += bar(i["albums"][0], i["albums"][1],
                         f'Albums: <b>{i["albums"][0]:,}</b> of {i["albums"][1]:,} looked up '
                         f'<span style="color:{MUTED};">({i["albums"][2]}% of plays covered)</span>')
        inner += bar(i["shows"][0] + i["shows"][1], i["shows"][2],
                     f'Podcast shows: <b>{i["shows"][0]}</b> of {i["shows"][2]} looked up'
                     + (f' <span style="color:{MUTED};">({i["shows"][1]} not on Spotify)</span>' if i["shows"][1] else ""))
        if i["rate_limited"]:
            inner += (f'<div style="{FONT}font-size:13px;color:{MUTED};">Spotify\'s daily lookup quota ran out at least '
                      f'once. That\'s expected; lookups resume on their own.</div>')
        body.append(section("Streaming-history import", inner))
    since = f' since {r["totals"]["since"]}' if r["totals"]["since"] else ""
    body.append(f'<tr><td class="px" style="padding:28px 28px 24px;{FONT}font-size:12px;color:{MUTED};line-height:1.5;">'
                f'{r["totals"]["plays"]:,} plays on record{since}. Full log: data/sync.log<br>'
                f'Sent by musicdashboard.py report · artwork and links from Spotify</td></tr>')

    preheader = (f'{r["plays"]} plays · {r["top_tracks"][0]["name"]} by {r["top_tracks"][0]["artist"]} on top'
                 if r["top_tracks"] else "A quiet day")
    phone = ("@media (max-width:520px){.outer{padding:12px 6px!important}.px{padding-left:16px!important;"
             "padding-right:16px!important}.tile{padding:10px 2px!important}.tile-value{font-size:17px!important}}")
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">'
            f'<style>{phone}</style></head><body style="margin:0;padding:0;background:{PAGE};">'
            f'<div style="display:none;max-height:0;overflow:hidden;">{h(preheader)}</div>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE};">'
            f'<tr><td class="outer" align="center" style="padding:24px 12px;">'
            f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;'
            f'background:#ffffff;border:1px solid {LINE};border-radius:12px;border-collapse:separate;">'
            + "".join(body) + "</table></td></tr></table></body></html>")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth", help="sign in to Spotify once and save the refresh token")
    sub.add_parser("sync", help="save new recent plays and podcast episodes (playing now or by resume point)")
    sub.add_parser("art", help="fetch cover art and release years for saved albums that don't have them yet")
    imp = sub.add_parser("import", help="read the privacy data export in data/export/ into the listening history")
    imp.add_argument("--limit", type=int, help="look up at most this many tracks/shows this run")
    sub.add_parser("build", help="write the site's data file, docs/data/listening.js")
    wrap = sub.add_parser("wrapped", help="write a year in review for docs/wrapped.html (default: every year)")
    wrap.add_argument("years", nargs="*", type=int)
    sub.add_parser("publish", help="commit and push docs/data/ to GitHub when it changed (sync.bat runs this)")
    rep = sub.add_parser("report", help="email the daily summary (sync.bat runs this; sends once a day)")
    rep.add_argument("--force", action="store_true", help="send now, even if today's report went out")
    rep.add_argument("--dry-run", action="store_true", help="print the report instead of sending it")
    args = parser.parse_args()
    load_env()
    try:
        if args.command == "import":
            import_history(args.limit)
        elif args.command == "wrapped":
            wrapped(args.years)
        elif args.command == "report":
            report(args.force, args.dry_run)
        elif args.command == "publish":
            publish()
        else:
            {"auth": auth, "sync": sync, "art": fill_art, "build": build}[args.command]()
    except RateLimited as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
