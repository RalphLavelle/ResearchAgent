import { NgOptimizedImage } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { TopicService } from '../topic/topic.service';

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
  date: string;
  url: string;
  summary: string;
  thumbnailUrl: string | null;
}

/** Root JSON shape from ``GET /api/<db>/events``. */
export interface EventsPayload {
  generated: string;
  events: ResearchEvent[];
}

@Component({
  selector: 'app-list',
  imports: [NgOptimizedImage],
  templateUrl: './list.html',
  styleUrl: './list.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ListComponent {
  /** Loaded snapshot: database-backed events + generation time. */
  protected readonly payload = signal<EventsPayload | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  /**
   * Defensive fallback: event IDs whose poster failed to load even though the
   * pipeline cached it in MongoDB. Tracking failures lets the template swap in
   * the 🎸 placeholder instead of a broken-image icon.
   */
  protected readonly posterErrors = signal<ReadonlySet<string>>(new Set());
  /** When set, only events for this venue filter key are shown. Click again to clear. */
  protected readonly activeVenueFilterKey = signal<string | null>(null);

  /** Events after optional venue filter is applied. */
  protected readonly visibleEvents = computed(() => {
    const data = this.payload();
    if (!data) {
      return [] as ResearchEvent[];
    }
    const filterKey = this.activeVenueFilterKey();
    if (!filterKey) {
      return data.events;
    }
    return data.events.filter((ev) => this.venueFilterKey(ev) === filterKey);
  });

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  readonly #topic = inject(TopicService);

  constructor() {
    // Reload when topics.json finishes and the active db name is known.
    effect(() => {
      const db = this.#topic.active().db;
      if (!this.#topic.loading()) {
        this.#loadEvents(db);
      }
    });
  }

  /**
   * Root-absolute poster URL for ``ngSrc`` — API paths or remote http(s) URLs.
   */
  protected posterSrc(url: string | null): string | null {
    if (!url) {
      return null;
    }
    if (url.startsWith('http://') || url.startsWith('https://')) {
      return url;
    }
    return url.startsWith('/') ? url : `/${url}`;
  }

  /** Toggle the venue filter on or off for one event row. */
  protected toggleVenueFilter(ev: ResearchEvent): void {
    const key = this.venueFilterKey(ev);
    if (!key) {
      return;
    }
    this.activeVenueFilterKey.update((current) => (current === key ? null : key));
  }

  /** Stable filter key — prefers venues-collection id, falls back to canonical name. */
  protected venueFilterKey(ev: ResearchEvent): string | null {
    const id = (ev.venueId ?? '').trim();
    if (id) {
      return `id:${id}`;
    }
    const name = ev.venue.trim().toLowerCase();
    return name ? `name:${name}` : null;
  }

  /** Extract a plain venue name even if the API ever sends a nested object. */
  #venueName(venue: ResearchEvent['venue'] | { name?: string }): string {
    if (typeof venue === 'string') {
      return venue.trim();
    }
    if (venue && typeof venue === 'object' && 'name' in venue) {
      return String(venue.name ?? '').trim();
    }
    return '';
  }

  /** Hook for the poster `<img>`'s `(error)` event. */
  protected onPosterError(eventId: string): void {
    this.posterErrors.update((current) => {
      if (current.has(eventId)) {
        return current;
      }
      const next = new Set(current);
      next.add(eventId);
      return next;
    });
  }

  /** GET ``/api/<db>/events`` from the Python API (proxied in dev). */
  #loadEvents(db: string): void {
    this.loading.set(true);
    this.error.set(null);
    const url = `/api/${db}/events?t=${Date.now()}`;

    this.#http
      .get<EventsPayload>(url)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          this.activeVenueFilterKey.set(null);
          this.payload.set({
            ...data,
            events: data.events.map((ev) => this.#normalizeEvent(ev)),
          });
          this.loading.set(false);
        },
        error: () => {
          this.error.set(
            `Could not load events for topic database "${db}". ` +
              'Run the research pipeline and ensure MONGODB_URI is set and `python -m agent api` is running.'
          );
          this.loading.set(false);
        },
      });
  }

  /** Coerce API rows so ``venue`` is always a plain name string in the UI. */
  #normalizeEvent(
    raw: ResearchEvent & {
      venue?: unknown;
      venueId?: unknown;
      venue_id?: unknown;
    },
  ): ResearchEvent {
    const venueName = this.#venueName(
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
      venue: venueName || String(nestedVenue?.name ?? '').trim(),
      location: String(raw.location ?? '').trim(),
      venueId,
    };
  }
}
