# YouTube integration (Task 6)

## Investigation summary

**Yes — this is feasible** using the official [YouTube Data API v3](https://developers.google.com/youtube/v3).

### What you need

1. A [Google Cloud project](https://console.cloud.google.com/) with **YouTube Data API v3** enabled.
2. An **API key** (server-side only — never expose it in the Angular app).
3. Add `YOUTUBE_API_KEY=...` to `.env` on the machine running `agent api`.

No OAuth is required for read-only video search.

### Quota and cost

| Item | Detail |
|------|--------|
| Price | Free (no paid tier) |
| Default daily quota | 10,000 units per project |
| `search.list` cost | **100 units per request** (~100 artist lookups/day) |
| `videos.list` | 1 unit (not needed for embed — we only store the video id) |

Quota resets at midnight Pacific Time. Extra quota requires a Google audit form; you cannot buy more.

### How lookup works here

1. The events list exposes `youtubeEligible: true` for gigs with a recognisable act name that are **not** tribute/cover/open-mic/DJ-style listings (see `src/agent/youtube.py`).
2. When the user clicks **Watch on YouTube**, the app calls `GET /api/<db>/events/<eventId>/youtube`.
3. The server searches YouTube once per event (`q="<act> music"`, `type=video`, `videoEmbeddable=true`), caches `youtube_video_id` on the MongoDB event document, and returns the id.
4. The modal embeds `https://www.youtube.com/embed/<videoId>` — no further API calls for playback.

Caching keeps daily quota usage close to “one search per distinct act the user actually clicks”, not one search per page load.

### Setup checklist

```text
Google Cloud Console → APIs & Services → Enable YouTube Data API v3
                  → Credentials → Create API key
                  → (recommended) Restrict key to YouTube Data API v3 + your server IP
.env → YOUTUBE_API_KEY=your_key_here
Restart `agent api`
```

If the key is missing, eligible events still show the button; the modal explains that YouTube lookup is not configured.
