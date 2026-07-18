/** One row from the topic's MongoDB-backed events API. */
export interface ResearchEvent {
  /** Stable event id — not displayed; use for `track` / poster error tracking. */
  id: string;
  eventName: string;
  /** Canonical venue name only — never the nested MongoDB `{ name, id }` object. */
  venue: string;
  /** Suburb or city shown beside the venue name in the UI. */
  location: string;
  /** Venues-collection id — used for filtering; not shown in the UI. */
  venueId: string | null;
  /** Display label, e.g. "Sat 18 Jul" — grouping rows in the list. */
  date: string;
  /** Machine-readable ISO date (YYYY-MM-DD) for structured data; null when undated. */
  isoDate: string | null;
  url: string;
  summary: string;
  thumbnailUrl: string | null;
  /** Filter tags assigned by the pipeline (max 3). */
  tags: string[];
  /** True when a YouTube preview button should appear (non-tribute named act). */
  youtubeEligible: boolean;
}

/** Root JSON shape from ``GET /api/<db>/events``. */
export interface EventsPayload {
  generated: string;
  events: ResearchEvent[];
}

/** Extract a plain venue name even if the API ever sends a nested object. */
function venueName(venue: ResearchEvent['venue'] | { name?: string }): string {
  if (typeof venue === 'string') {
    return venue.trim();
  }
  if (venue && typeof venue === 'object' && 'name' in venue) {
    return String(venue.name ?? '').trim();
  }
  return '';
}

/** Coerce API rows so ``venue`` is always a plain name string in the UI. */
export function normalizeResearchEvent(
  raw: ResearchEvent & {
    venue?: unknown;
    venueId?: unknown;
    venue_id?: unknown;
  },
): ResearchEvent {
  const name = venueName(
    raw.venue as ResearchEvent['venue'] | { name?: string; id?: string },
  );
  const nestedVenue =
    raw.venue && typeof raw.venue === 'object' && !Array.isArray(raw.venue)
      ? (raw.venue as { name?: string; id?: string })
      : null;

  const venueIdRaw = raw.venueId ?? raw.venue_id ?? nestedVenue?.id;
  const venueId =
    typeof venueIdRaw === 'string' && venueIdRaw.trim() ? venueIdRaw.trim() : null;

  return {
    ...raw,
    venue: name || String(nestedVenue?.name ?? '').trim(),
    location: String(raw.location ?? '').trim(),
    venueId,
    isoDate:
      typeof raw.isoDate === 'string' && raw.isoDate.trim() ? raw.isoDate.trim() : null,
    tags: Array.isArray(raw.tags)
      ? raw.tags.map((tag) => String(tag).trim().toLowerCase()).filter(Boolean).slice(0, 3)
      : [],
    youtubeEligible: raw.youtubeEligible === true,
  };
}

/** True when the event has a poster URL the UI can load (not the 🎸 placeholder). */
export function eventHasPoster(ev: ResearchEvent): boolean {
  return posterSrc(ev.thumbnailUrl) !== null;
}

/** Venue line for spotlight cards (name + location when present). */
export function featuredVenueLine(ev: ResearchEvent): string {
  const parts = [ev.venue.trim(), ev.location.trim()].filter(Boolean);
  return parts.join(', ');
}

/** Root-absolute poster URL for ``ngSrc`` — API paths or remote http(s) URLs. */
export function posterSrc(url: string | null): string | null {
  if (!url) {
    return null;
  }
  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url;
  }
  return url.startsWith('/') ? url : `/${url}`;
}
