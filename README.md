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
