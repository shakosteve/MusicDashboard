"""MusicDashboard: collect my Spotify listening (music and podcasts) for a personal site.

    python musicdashboard.py auth    one-time: sign in to Spotify in the browser, saves the refresh token to .env
    python musicdashboard.py sync    save new recently played tracks, and the podcast episode playing now
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

Spotify only returns the last 50 played tracks and never includes podcast episodes there, so `sync`
should run often (every 30-60 minutes, via sync.bat in Task Scheduler). Episodes are caught by
checking what's playing at each run. The full history comes from the privacy data export
("Extended streaming history"): unzip its Streaming_History_Audio_*.json files into data/export/ and
run `import`. Plays in recently_played.jsonl after the export's last play are added on top of it.

Data (in data/, not committed):
    recently_played.jsonl   one play per line, oldest first, unique by played_at
    episodes.json           podcast episodes seen playing, by episode URI, with the furthest progress
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
from collections import Counter
from datetime import datetime, timezone
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
MIN_PLAY_MS = 30_000  # Spotify counts a stream from 30 seconds
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


def sync():
    session = api_session()
    new = sync_plays(session)
    print(f"{len(new)} new play(s)" + (f", latest: {new[-1]['name']} - {', '.join(new[-1]['artists'])}" if new else ""))
    episode = sync_episode(session)
    if episode:
        print(f"Podcast playing: {episode['show']} - {episode['name']}")


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


def try_fetch(session, url):
    """fetch, but None for a track or episode Spotify no longer has (404 and the like)."""
    try:
        return fetch(session, url)
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
    show_uris = {name: s.get("uri") for name, s in shows.items() if "uri" in s}
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
    if limit is not None:
        wanted = wanted[:limit]
        wanted_shows = wanted_shows[:limit - len(wanted)]
    session = api_session() if wanted or wanted_shows else None

    def save():
        write_atomic(TRACKS, json.dumps(tracks, ensure_ascii=False))
        write_atomic(ART, json.dumps(art, indent=2, ensure_ascii=False))

    try:
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
        for name in wanted_shows:
            episode = try_fetch(session, f"{API}/episodes/{latest_episode[name].split(':')[-1]}") or {}
            show_uris[name] = (episode.get("show") or {}).get("uri")
            remember_art(art, episode.get("show"))
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


def build():
    """Write docs/data/listening.js (a script, so docs/index.html also works opened as a file)."""
    art = load_art()
    def read_plays(path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()] if path.exists() else []

    plays = read_plays(HISTORY)
    exported_to = plays[-1]["played_at"][:19] if plays else ""
    plays += [p for p in read_plays(PLAYS) if p["played_at"][:19] > exported_to]
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
                            for uri, e in episodes.items()), key=lambda e: e["last_seen"], reverse=True)[:12],
        "top_shows": [{"name": s["name"], "plays": s["plays"], "hours": round(s["ms_played"] / 3_600_000),
                       "image": art.get(s["uri"], {}).get("image"), "url": open_url(s["uri"])}
                      for s in sorted(shows.values(), key=lambda s: -s["ms_played"])[:12]],
    }
    out = ROOT / "docs" / "data" / "listening.js"
    write_atomic(out, "window.LISTENING = " + json.dumps(site, ensure_ascii=False, indent=1) + ";\n")
    print(f"Wrote {out.relative_to(ROOT)}: {len(plays)} plays, {len(artists)} artists, {len(albums)} albums.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth", help="sign in to Spotify once and save the refresh token")
    sub.add_parser("sync", help="save new recent plays and the podcast episode playing now")
    sub.add_parser("art", help="fetch cover art and release years for saved albums that don't have them yet")
    imp = sub.add_parser("import", help="read the privacy data export in data/export/ into the listening history")
    imp.add_argument("--limit", type=int, help="look up at most this many tracks/shows this run")
    sub.add_parser("build", help="write the site's data file, docs/data/listening.js")
    args = parser.parse_args()
    load_env()
    try:
        if args.command == "import":
            import_history(args.limit)
        else:
            {"auth": auth, "sync": sync, "art": fill_art, "build": build}[args.command]()
    except RateLimited as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
