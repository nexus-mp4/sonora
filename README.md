# Sonora

Two sources, one player:

- **YouTube Music** (default): browse, search, charts, moods, albums, artists, playlists, radio, lyrics, and your library after sign-in. Powered by `ytmusicapi`; audio is resolved with `yt-dlp`.
- **Local**: your own files, exactly as before (folder picker, tags, artwork lookup, playlists). Switch with the toggle in the header.

## Run

```
pip install -r requirements.txt
python3 server.py
```

Open http://localhost:8000. The server only listens on your own computer.

Files: `index.html` (app), `yt.js` (YouTube Music side), `server.py` (API), optional `songs/` folder (auto-listed, no `songs.json` needed).

## Sign in (optional)

Settings → YouTube Music → Sign in. Paste request headers from a signed-in music.youtube.com tab (F12 → Network → filter "browse" → a POST request → copy request headers). Saved to `browser.json` beside `server.py`; keep it private. Sign-in enables Library, Liked songs, like syncing and "Save to YouTube playlist".

## Options (environment variables)

`PORT`, `HOST`, `YTM_LANGUAGE`, `YTM_LOCATION` (country code), `YTM_COUNTRY` (charts), `YTDLP_COOKIES` (cookies file for yt-dlp if YouTube asks for sign-in).

## Notes

ytmusicapi and yt-dlp are unofficial and can break when YouTube changes; `pip install -U ytmusicapi yt-dlp` usually fixes it. Streaming through yt-dlp is outside YouTube's terms of service, so use it for personal use only.

## Deploy on Render

1. Push this folder to a GitHub repo (`browser.json` and `cookies.txt` are git-ignored).
2. In Render choose New → Blueprint (it reads `render.yaml`), or New → Web Service with build `pip install -r requirements.txt` and start `gunicorn server:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT`.
3. Set environment variables:
   - `SONORA_PASSWORD` (required in practice): the site asks for it (any username). Without it anyone with the URL can use your account.
   - `YTM_BROWSER_JSON` (optional): paste the contents of a `browser.json` made locally (sign in locally once, then copy the file). Sign-in from the Settings screen is disabled on the server when this is set.
   - `YTDLP_COOKIES` (optional): add a cookies.txt as a Render Secret File and set this to `/etc/secrets/cookies.txt`.
4. On the server, audio is relayed through the app (YouTube links only work for the IP that requested them), which uses your instance's bandwidth.

Caveats: YouTube often blocks datacenter IPs for yt-dlp, so playback may fail without cookies and can break at any time. Free instances sleep when idle and the first request after a sleep takes about a minute. The Local source still works on a deployment, because it reads files in your browser, but the `songs/` folder is not included.
