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
import { EmailSignupModalComponent } from './email-signup-modal/email-signup-modal';

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
  /** Filter tags assigned by the pipeline (max 3). */
  tags: string[];
}

/** Root JSON shape from ``GET /api/<db>/events``. */
export interface EventsPayload {
  generated: string;
  events: ResearchEvent[];
}

@Component({
  selector: 'app-list',
  imports: [NgOptimizedImage, EmailSignupModalComponent],
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
  /** When set, only events containing this tag are shown. Click again to clear. */
  protected readonly activeTagFilter = signal<string | null>(null);

  /** Sorted distinct tags across all loaded events (for the filter bar). */
  protected readonly distinctTags = computed(() => {
    const data = this.payload();
    if (!data) {
      return [] as string[];
    }
    const found = new Set<string>();
    for (const ev of data.events) {
      for (const tag of ev.tags ?? []) {
        const label = tag.trim().toLowerCase();
        if (label) {
          found.add(label);
        }
      }
    }
    return [...found].sort();
  });

  /** Events after optional venue and tag filters are applied. */
  protected readonly visibleEvents = computed(() => {
    const data = this.payload();
    if (!data) {
      return [] as ResearchEvent[];
    }
    const venueKey = this.activeVenueFilterKey();
    const tag = this.activeTagFilter();
    return data.events.filter((ev) => {
      if (venueKey && this.venueFilterKey(ev) !== venueKey) {
        return false;
      }
      if (tag && !(ev.tags ?? []).includes(tag)) {
        return false;
      }
      return true;
    });
  });

  /** Four random spotlight cards — reshuffled on every page load. */
  protected readonly featuredEvents = signal<ResearchEvent[]>([]);
  /** Carousel offset when fewer than four cards fit on screen. */
  protected readonly featuredIndex = signal(0);
  /** When true, the weekly email signup modal is open. */
  protected readonly emailSignupOpen = signal(false);

  /** Active topic MongoDB name — passed to the signup modal API call. */
  protected readonly activeDb = computed(() => this.#topic.active().db);

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

  /** Toggle the tag filter on or off. */
  protected toggleTagFilter(tag: string): void {
    const key = tag.trim().toLowerCase();
    if (!key) {
      return;
    }
    this.activeTagFilter.update((current) => (current === key ? null : key));
  }

  /** True when an event row carries a given tag. */
  protected eventHasTag(ev: ResearchEvent, tag: string): boolean {
    return (ev.tags ?? []).includes(tag);
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

  /** Venue line for spotlight cards (name + location when present). */
  protected featuredVenueLine(ev: ResearchEvent): string {
    return this.venueButtonLabel(ev);
  }

  /** Venue filter button label: ``Name, Location``. */
  protected venueButtonLabel(ev: ResearchEvent): string {
    const parts = [ev.venue.trim(), ev.location.trim()].filter(Boolean);
    return parts.join(', ');
  }

  protected showFeaturedCarouselNav(): boolean {
    return this.featuredEvents().length > 1;
  }

  protected openEmailSignup(): void {
    this.emailSignupOpen.set(true);
  }

  protected closeEmailSignup(): void {
    this.emailSignupOpen.set(false);
  }

  protected prevFeatured(): void {
    const count = this.featuredEvents().length;
    if (count <= 1) {
      return;
    }
    this.featuredIndex.update((index) => (index - 1 + count) % count);
  }

  protected nextFeatured(): void {
    const count = this.featuredEvents().length;
    if (count <= 1) {
      return;
    }
    this.featuredIndex.update((index) => (index + 1) % count);
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
          this.activeTagFilter.set(null);
          const events = data.events.map((ev) => this.#normalizeEvent(ev));
          this.featuredIndex.set(0);
          this.featuredEvents.set(this.#pickFeaturedEvents(events));
          this.payload.set({
            ...data,
            events,
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
      tags: Array.isArray(raw.tags)
        ? raw.tags.map((tag) => String(tag).trim().toLowerCase()).filter(Boolean).slice(0, 3)
        : [],
    };
  }

  /** Shuffle and take up to four events for the spotlight carousel. */
  #pickFeaturedEvents(events: ResearchEvent[]): ResearchEvent[] {
    if (events.length === 0) {
      return [];
    }
    const pool = [...events];
    for (let i = pool.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    return pool.slice(0, Math.min(4, pool.length));
  }
}
