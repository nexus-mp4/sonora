#!/usr/bin/env python3
"""Sonora server.

Serves the web app and exposes ytmusicapi (browse, search, library, lyrics) and
yt-dlp (audio stream resolution) as a small JSON API on localhost.

The local-library side of the app never needs this server, except that it also
serves the optional ./songs folder.

Run:  python3 server.py   then open http://localhost:8000
"""
from __future__ import annotations

import hmac
import importlib.util
import os
import re
import shutil
import tempfile
import threading
import time
from functools import wraps
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory
from ytmusicapi import YTMusic

try:  # name changed between ytmusicapi releases
    from ytmusicapi import setup_browser as _setup_browser
except ImportError:  # pragma: no cover
    from ytmusicapi import setup as _setup_browser

ROOT = Path(__file__).resolve().parent
AUTH_FILE = ROOT / "browser.json"
LANGUAGE = os.environ.get("YTM_LANGUAGE", "en")
LOCATION = os.environ.get("YTM_LOCATION", "")  # ISO country code, e.g. "US"; empty = default
CHARTS_COUNTRY = os.environ.get("YTM_COUNTRY", "ZZ")  # "ZZ" = global charts
# --- hosting (Render etc.) -------------------------------------------------------
ON_HOST = bool(os.environ.get("RENDER") or os.environ.get("PORT"))
# Stream URLs from YouTube are tied to the IP that requested them. On a server the browser has a
# different IP, so audio must be relayed ("proxy"). Locally a plain redirect is lighter.
STREAM_MODE = os.environ.get("STREAM_MODE") or ("proxy" if ON_HOST else "redirect")
PASSWORD = os.environ.get("SONORA_PASSWORD", "")  # set this on any public deployment
AUTH_ENV = os.environ.get("YTM_BROWSER_JSON", "")  # contents of browser.json, as a secret env var
if AUTH_ENV:  # hosts have throwaway disks, so the account comes from the environment
    AUTH_FILE = Path(tempfile.gettempdir()) / "sonora-browser.json"
    AUTH_FILE.write_text(AUTH_ENV)
    os.chmod(AUTH_FILE, 0o600)
COOKIES = os.environ.get("YTDLP_COOKIES", "")
if COOKIES and Path(COOKIES).is_file():  # secret files are read-only; yt-dlp wants to write back
    _c = Path(tempfile.gettempdir()) / "sonora-cookies.txt"
    shutil.copy(COOKIES, _c)
    COOKIES = str(_c)
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav", ".weba", ".webm", ".mp4"}

app = Flask(__name__)
_http = requests.Session()


@app.before_request
def gate():
    if not PASSWORD or request.path == "/healthz":
        return None
    a = request.authorization
    if a and hmac.compare_digest((a.password or "").encode(), PASSWORD.encode()):
        return None
    return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="Sonora"'})


@app.get("/healthz")
def healthz():
    return "ok"

# --------------------------------------------------------------------------- client

_lock = threading.RLock()
_state: dict = {"yt": None, "auth": False}


class NeedAuth(Exception):
    pass


def yt() -> YTMusic:
    """Shared YTMusic client. Authenticated when browser.json exists and works, guest otherwise."""
    with _lock:
        if _state["yt"] is None:
            kw = {"language": LANGUAGE}
            if LOCATION:
                kw["location"] = LOCATION
            if AUTH_FILE.exists():
                try:
                    _state["yt"] = YTMusic(str(AUTH_FILE), **kw)
                    _state["auth"] = True
                except Exception:
                    app.logger.warning("browser.json could not be used; continuing as guest", exc_info=True)
            if _state["yt"] is None:
                _state["yt"] = YTMusic(**kw)
                _state["auth"] = False
        return _state["yt"]


def reset_client() -> None:
    with _lock:
        _state.update(yt=None, auth=False)


def need_auth() -> YTMusic:
    client = yt()
    if not _state["auth"]:
        raise NeedAuth()
    return client


def api(fn):
    """JSON in/out with uniform error handling."""

    @wraps(fn)
    def wrapper(*a, **k):
        try:
            return jsonify(fn(*a, **k))
        except NeedAuth:
            return jsonify(error="Sign in to YouTube Music in Settings to use this.", auth=True), 401
        except ValueError as e:
            return jsonify(error=str(e)), 400
        except Exception as e:  # noqa: BLE001
            app.logger.exception("api error")
            return jsonify(error=f"{type(e).__name__}: {e}"), 502

    return wrapper


# --------------------------------------------------------------------------- normalising

_IMG_RE = re.compile(r"=[wsh]\d+(-[a-z]\d*)*(-[a-z]+)*$")
_VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def check_vid(vid: str) -> str:
    if not _VID_RE.match(vid or ""):
        raise ValueError("Bad video id")
    return vid


def best_thumb(th, size: int = 544) -> str:
    if not th:
        return ""
    if isinstance(th, dict):
        th = [th]
    biggest = max(th, key=lambda x: (x or {}).get("width", 0) or 0) or {}
    url = biggest.get("url", "")
    if "googleusercontent.com" in url or "ggpht.com" in url:
        url = re.sub(r"=w\d+-h\d+.*$", f"=w{size}-h{size}-l90-rj", url)
    return url


def secs(v) -> int:
    if v in (None, ""):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    total = 0
    for part in str(v).split(":"):
        if part.strip().isdigit():
            total = total * 60 + int(part)
    return total


def names(arts) -> str:
    if not arts:
        return ""
    if isinstance(arts, str):
        return arts
    return ", ".join(a.get("name", "") for a in arts if isinstance(a, dict) and a.get("name"))


def first_artist_id(arts) -> str:
    if isinstance(arts, list):
        for a in arts:
            if isinstance(a, dict) and a.get("id"):
                return a["id"]
    return ""


def author_name(a) -> str:
    if isinstance(a, str):
        return a
    if isinstance(a, dict):
        return a.get("name", "")
    if isinstance(a, list):
        return names(a)
    return ""


def clean(it) -> list:
    return [x for x in it if x]


def track(d: dict, cover: str = "", album: str = "", album_id: str = "", artist: str = "", artist_id: str = ""):
    vid = d.get("videoId")
    if not vid:
        return None
    arts = d.get("artists") or d.get("artist")
    alb = d.get("album")
    if isinstance(alb, dict):
        aname, aid = alb.get("name", ""), alb.get("id", "") or album_id
    else:
        aname, aid = (alb if isinstance(alb, str) else "") or album, album_id
    return {
        "k": "song",
        "id": "yt:" + vid,
        "vid": vid,
        "title": d.get("title", ""),
        "artist": names(arts) or artist or d.get("byline", "") or "",
        "artistId": first_artist_id(arts) or artist_id,
        "album": aname,
        "albumId": aid or "",
        "dur": secs(d.get("duration_seconds") or d.get("duration") or d.get("length")),
        "cover": best_thumb(d.get("thumbnails") or d.get("thumbnail")) or cover,
        "no": d.get("trackNumber") or 0,
    }


def item(d):
    """Any search/home/library result -> {k: song|album|artist|playlist, ...} or None."""
    if not isinstance(d, dict):
        return None
    if d.get("videoId"):
        return track(d)
    bid = d.get("browseId") or ""
    pid = d.get("playlistId") or ""
    rt = d.get("resultType")
    cover = best_thumb(d.get("thumbnails"))
    if bid.startswith("MPRE") or rt in ("album", "single", "ep"):
        if not bid:
            return None
        artist = names(d.get("artists")) or (d.get("artist") if isinstance(d.get("artist"), str) else "")
        sub = " · ".join(x for x in [artist, d.get("type"), str(d.get("year") or "")] if x)
        return {"k": "album", "id": bid, "title": d.get("title", ""), "sub": sub, "cover": cover}
    if bid.startswith("UC") or bid.startswith("MPLA") or rt == "artist":
        if not bid:
            return None
        subs = d.get("subscribers")
        return {
            "k": "artist",
            "id": bid,
            "title": d.get("artist") or d.get("title") or "",
            "sub": f"{subs} subscribers" if subs else "Artist",
            "cover": cover,
        }
    if bid.startswith("VL"):
        pid = bid[2:]
    if pid:
        sub = author_name(d.get("author")) or d.get("description") or (f"{d['count']} songs" if d.get("count") else "")
        if d.get("itemCount"):
            sub = " · ".join(x for x in [sub, f"{d['itemCount']} songs"] if x)
        return {"k": "playlist", "id": pid, "title": d.get("title", ""), "sub": sub, "cover": cover}
    return None


# --------------------------------------------------------------------------- API

@app.get("/api/status")
@api
def status():
    yt()
    return {
        "auth": _state["auth"],
        "stream": importlib.util.find_spec("yt_dlp") is not None,
        "ytmusicapi": _pkg_version("ytmusicapi"),
    }


def _pkg_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return ""


@app.get("/api/home")
@api
def home():
    shelves = []
    for s in yt().get_home(limit=6):
        items = clean(item(c) for c in s.get("contents", []) or [])
        if items:
            shelves.append({"title": s.get("title", ""), "items": items})
    return {"shelves": shelves}


@app.get("/api/search")
@api
def search():
    q = request.args.get("q", "").strip()
    flt = request.args.get("filter") or None
    if not q:
        return {"shelves": []}
    if flt not in (None, "songs", "videos", "albums", "artists", "playlists"):
        raise ValueError("Unknown filter")
    results = yt().search(q, filter=flt, limit=30 if flt else 20)
    shelves: list[dict] = []
    index: dict[str, dict] = {}
    for r in results:
        it = item(r)
        if not it:
            continue
        title = (r.get("category") or {"songs": "Songs", "videos": "Videos", "albums": "Albums",
                                       "artists": "Artists", "playlists": "Playlists"}.get(flt or "", "Results"))
        shelf = index.get(title)
        if shelf is None:
            shelf = index[title] = {"title": title, "items": []}
            shelves.append(shelf)
        shelf["items"].append(it)
    return {"shelves": shelves}


@app.get("/api/album/<bid>")
@api
def album(bid):
    a = yt().get_album(bid)
    cover = best_thumb(a.get("thumbnails"))
    artist = names(a.get("artists"))
    artist_id = first_artist_id(a.get("artists"))
    tracks = clean(
        track(t, cover=cover, album=a.get("title", ""), album_id=bid, artist=artist, artist_id=artist_id)
        for t in a.get("tracks", [])
    )
    return {
        "k": "album",
        "id": bid,
        "title": a.get("title", ""),
        "artist": artist,
        "artistId": artist_id,
        "year": str(a.get("year") or ""),
        "cover": cover,
        "dur": secs(a.get("duration_seconds")) or sum(t["dur"] for t in tracks),
        "tracks": tracks,
    }


@app.get("/api/artist/<cid>")
@api
def artist(cid):
    a = yt().get_artist(cid)

    def results(key):
        v = a.get(key)
        if isinstance(v, dict):
            return v.get("results") or []
        return v or []

    subs = a.get("subscribers")
    return {
        "k": "artist",
        "id": cid,
        "name": a.get("name", ""),
        "sub": f"{subs} subscribers" if subs else "",
        "cover": best_thumb(a.get("thumbnails")),
        "songs": clean(track(x) for x in results("songs")),
        "albums": clean(item(x) for x in results("albums")),
        "singles": clean(item(x) for x in results("singles")),
        "related": clean(item(x) for x in results("related")),
    }


@app.get("/api/playlist/<pid>")
@api
def playlist(pid):
    if pid == "LM":
        p = need_auth().get_liked_songs(limit=500)
    else:
        p = yt().get_playlist(pid, limit=300)
    tracks = clean(track(t) for t in p.get("tracks", []))
    return {
        "k": "playlist",
        "id": pid,
        "title": "Liked songs" if pid == "LM" else p.get("title", ""),
        "author": author_name(p.get("author")),
        "cover": best_thumb(p.get("thumbnails")) or (tracks[0]["cover"] if tracks else ""),
        "dur": secs(p.get("duration_seconds")) or sum(t["dur"] for t in tracks),
        "tracks": tracks,
    }


@app.get("/api/watch/<vid>")
@api
def watch(vid):
    """Radio: the song followed by YouTube Music's suggestions."""
    check_vid(vid)
    w = yt().get_watch_playlist(videoId=vid, limit=50, radio=True)
    return {"tracks": clean(track(t) for t in w.get("tracks", []))}


@app.get("/api/lyrics/<vid>")
@api
def lyrics(vid):
    check_vid(vid)
    empty = {"synced": [], "plain": ""}
    try:
        lid = yt().get_watch_playlist(videoId=vid).get("lyrics")
        if not lid:
            return empty
        try:
            data = yt().get_lyrics(lid, timestamps=True)
        except TypeError:  # older ytmusicapi
            data = yt().get_lyrics(lid)
    except Exception:  # noqa: BLE001  (no lyrics is not an error)
        return empty
    raw = (data or {}).get("lyrics") if isinstance(data, dict) else None
    if not raw:
        return empty
    if isinstance(raw, str):
        return {"synced": [], "plain": raw}
    synced = []
    for line in raw:
        text = line.get("text") if isinstance(line, dict) else getattr(line, "text", None)
        start = line.get("start_time") if isinstance(line, dict) else getattr(line, "start_time", None)
        if text is None or start is None:
            continue
        synced.append({"t": start / 1000, "s": text})
    return {"synced": synced, "plain": "\n".join(x["s"] for x in synced)}


@app.get("/api/explore")
@api
def explore():
    out: dict = {"shelves": [], "moods": []}
    try:
        charts = yt().get_charts(CHARTS_COUNTRY)
        titles = {"songs": "Top songs", "videos": "Top videos", "trending": "Trending", "artists": "Top artists"}
        for key, title in titles.items():
            v = charts.get(key)
            lst = v.get("items") if isinstance(v, dict) else v
            items = clean(item(x) for x in (lst or []))
            if items:
                out["shelves"].append({"title": title, "items": items[:20]})
    except Exception:  # noqa: BLE001
        app.logger.warning("charts unavailable", exc_info=True)
    try:
        for group, entries in yt().get_mood_categories().items():
            out["moods"].append(
                {"group": group, "items": [{"title": e["title"], "params": e["params"]} for e in entries]}
            )
    except Exception:  # noqa: BLE001
        app.logger.warning("moods unavailable", exc_info=True)
    return out


@app.get("/api/mood/<path:params>")
@api
def mood(params):
    return {"items": clean(item(x) for x in yt().get_mood_playlists(params))}


@app.get("/api/library")
@api
def library():
    c = need_auth()
    playlists = [p for p in clean(item(x) for x in c.get_library_playlists(limit=50)) if p["id"] not in ("LM", "SE")]
    return {
        "playlists": playlists,
        "albums": clean(item(x) for x in c.get_library_albums(limit=50)),
        "artists": clean(item(x) for x in c.get_library_subscriptions(limit=50)),
    }


@app.post("/api/rate")
@api
def rate():
    body = request.get_json(silent=True) or {}
    rating = body.get("rating")
    if rating not in ("LIKE", "INDIFFERENT", "DISLIKE"):
        raise ValueError("Bad rating")
    need_auth().rate_song(check_vid(body.get("vid", "")), rating)
    return {"ok": True}


@app.post("/api/playlist/add")
@api
def playlist_add():
    body = request.get_json(silent=True) or {}
    pid = body.get("playlistId") or ""
    if not pid:
        raise ValueError("Missing playlist")
    need_auth().add_playlist_items(pid, [check_vid(body.get("vid", ""))], duplicates=False)
    return {"ok": True}


@app.post("/api/auth")
@api
def auth_set():
    if AUTH_ENV:
        raise ValueError("Sign-in is set by the YTM_BROWSER_JSON environment variable on the server.")
    raw = ((request.get_json(silent=True) or {}).get("headers") or "").strip()
    if not raw:
        raise ValueError("Paste the request headers first.")
    tmp = ROOT / "browser.json.tmp"
    try:
        _setup_browser(filepath=str(tmp), headers_raw=raw)
        probe = YTMusic(str(tmp), language=LANGUAGE)
        info = probe.get_account_info()
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise ValueError(
            f"Those headers didn't work ({e}). Copy them from a POST request on a signed-in music.youtube.com tab."
        ) from e
    tmp.replace(AUTH_FILE)
    try:
        os.chmod(AUTH_FILE, 0o600)
    except OSError:
        pass
    reset_client()
    return {"ok": True, "account": (info or {}).get("accountName", "")}


@app.delete("/api/auth")
@api
def auth_clear():
    if AUTH_ENV:
        raise ValueError("Sign-in is set by the YTM_BROWSER_JSON environment variable on the server.")
    AUTH_FILE.unlink(missing_ok=True)
    reset_client()
    return {"ok": True}


# --- images (so the page can read pixels for the colour scheme) -------------------

_IMG_HOSTS = ("googleusercontent.com", "ggpht.com", "ytimg.com", "youtube.com")


@app.get("/api/img")
def img():
    u = request.args.get("u", "")
    p = urlparse(u)
    host = p.hostname or ""
    if p.scheme not in ("http", "https") or not any(host == d or host.endswith("." + d) for d in _IMG_HOSTS):
        abort(400)
    try:
        r = _http.get(u, timeout=10, allow_redirects=False)
    except requests.RequestException:
        abort(502)
    if r.status_code != 200:
        abort(502)
    return Response(
        r.content,
        content_type=r.headers.get("Content-Type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=604800"},
    )


# --- audio ------------------------------------------------------------------------

_stream_cache: dict[str, tuple[str, float]] = {}
_stream_hdrs: dict[str, dict] = {}


def resolve_stream(vid: str) -> str:
    now = time.time()
    hit = _stream_cache.get(vid)
    if hit and hit[1] > now:
        return hit[0]
    import yt_dlp  # imported lazily so the rest of the app works without it

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
    }
    if COOKIES:
        opts["cookiefile"] = COOKIES
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://music.youtube.com/watch?v={vid}", download=False)
    url = info["url"]
    _stream_hdrs[vid] = info.get("http_headers") or {}
    m = re.search(r"[?&]expire=(\d+)", url)
    expires = int(m.group(1)) - 300 if m else now + 3 * 3600
    if len(_stream_cache) > 300:
        _stream_cache.clear()
    _stream_cache[vid] = (url, expires)
    return url


def proxy_stream(vid: str, url: str):
    """Relay audio bytes (with Range support so seeking works)."""
    h = {k: v for k, v in (_stream_hdrs.get(vid) or {}).items() if k.lower() in ("user-agent", "accept", "accept-language")}
    if request.headers.get("Range"):
        h["Range"] = request.headers["Range"]
    r = _http.get(url, headers=h, stream=True, timeout=20)
    if r.status_code >= 400:
        _stream_cache.pop(vid, None)
        r.close()
        return jsonify(error=f"YouTube refused the audio request ({r.status_code})."), 502
    out = {k: r.headers[k] for k in ("Content-Type", "Content-Length", "Content-Range") if k in r.headers}
    out["Accept-Ranges"] = "bytes"

    def body():
        try:
            yield from r.iter_content(64 * 1024)
        finally:
            r.close()

    return Response(body(), status=r.status_code, headers=out)


@app.get("/api/stream/<vid>")
def stream(vid):
    try:
        url = resolve_stream(check_vid(vid))
        if STREAM_MODE != "proxy":
            return redirect(url, code=302)
        return proxy_stream(vid, url)
    except ValueError:
        abort(400)
    except ImportError:
        return jsonify(error="yt-dlp isn't installed. Run: pip install yt-dlp"), 503
    except Exception as e:  # noqa: BLE001
        app.logger.warning("stream failed for %s: %s", vid, e)
        return jsonify(error=str(e)), 502


def _short(e) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
    msg = re.sub(r"^\[youtube[^\]]*\]\s*\S+:\s*", "", msg).strip().splitlines()[0][:220] if msg.strip() else type(e).__name__
    if "not a bot" in msg or "Sign in to confirm" in msg:
        msg += " (YouTube is blocking this IP: provide cookies via YTDLP_COOKIES)"
    return msg


@app.get("/api/diag/<vid>")
def diag(vid):
    """Says why a song can't be streamed (shown by the app when playback fails)."""
    try:
        check_vid(vid)
        import yt_dlp  # noqa: F401
    except ImportError:
        return jsonify(ok=False, error="yt-dlp isn't installed on the server")
    except ValueError:
        return jsonify(ok=False, error="bad video id")
    try:
        url = resolve_stream(vid)
    except Exception as e:  # noqa: BLE001
        app.logger.warning("diag resolve failed for %s: %s", vid, e)
        return jsonify(ok=False, stage="resolve", error=_short(e))
    try:
        h = {k: v for k, v in (_stream_hdrs.get(vid) or {}).items() if k.lower() == "user-agent"}
        r = _http.get(url, headers={**h, "Range": "bytes=0-1"}, timeout=15, stream=True)
        code = r.status_code
        r.close()
    except requests.RequestException as e:
        return jsonify(ok=False, stage="fetch", error=f"couldn't download audio: {type(e).__name__}")
    if code >= 400:
        _stream_cache.pop(vid, None)
        return jsonify(ok=False, stage="fetch", error=f"YouTube refused the audio download (HTTP {code})")
    return jsonify(ok=True)


@app.get("/api/warm/<vid>")
def warm(vid):
    """Resolve the next track ahead of time so it starts instantly."""
    try:
        resolve_stream(check_vid(vid))
    except Exception:  # noqa: BLE001
        pass
    return jsonify(ok=True)


# --------------------------------------------------------------------------- static (explicit allow-list: browser.json stays private)

@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html", max_age=0)


@app.get("/yt.js")
def yt_js():
    return send_from_directory(ROOT, "yt.js", max_age=0)


@app.get("/songs/songs.json")
def songs_json():
    d = ROOT / "songs"
    if (d / "songs.json").is_file():
        return send_from_directory(d, "songs.json", max_age=0)
    files = []
    if d.is_dir():
        files = [
            p.relative_to(d).as_posix()
            for p in sorted(d.rglob("*"))
            if p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith(".")
        ]
    return jsonify([{"file": f} for f in files])


@app.get("/songs/<path:p>")
def songs_file(p):
    return send_from_directory(ROOT / "songs", p, conditional=True)


@app.get("/favicon.ico")
def favicon():
    return "", 204


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0" if ON_HOST else "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    if importlib.util.find_spec("yt_dlp") is None:
        print("! yt-dlp is not installed: browsing works, but songs will not play. pip install yt-dlp")
    print(f"Sonora is running at http://localhost:{port}  (Ctrl+C to stop)")
    app.run(host=host, port=port, threaded=True)
