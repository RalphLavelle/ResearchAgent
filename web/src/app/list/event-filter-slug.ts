import { ResearchEvent } from '../events/research-event.model';

/** Turn a display label into a URL segment (e.g. "The Triffid" → "the-triffid"). */
export function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/['']/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/** Internal venue filter key — matches ``ListComponent.venueFilterKey``. */
export function venueFilterKey(ev: ResearchEvent): string | null {
  const id = (ev.venueId ?? '').trim();
  if (id) {
    return `id:${id}`;
  }
  const name = ev.venue.trim().toLowerCase();
  return name ? `name:${name}` : null;
}

/** Resolve a venue slug from the URL to the internal filter key using loaded events. */
export function venueFilterKeyForSlug(
  events: ResearchEvent[],
  slug: string,
): string | null {
  const normalized = slug.trim().toLowerCase();
  if (!normalized) {
    return null;
  }
  for (const ev of events) {
    if (slugify(ev.venue) === normalized) {
      return venueFilterKey(ev);
    }
  }
  return null;
}
