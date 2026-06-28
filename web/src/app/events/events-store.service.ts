import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';

import { EventsPayload, normalizeResearchEvent } from './research-event.model';

/**
 * In-memory cache of the events list for the active topic database.
 *
 * Tag and venue filters live on separate routes (`/tags/...`, `/venues/...`) that
 * remount the home page, which would otherwise refetch from MongoDB on every
 * filter click. This service loads once per topic `db` and serves cached rows
 * for instant client-side filtering.
 */
@Injectable({ providedIn: 'root' })
export class EventsStore {
  /** Latest payload for the cached topic database (or null before first load). */
  readonly payload = signal<EventsPayload | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  readonly #http = inject(HttpClient);
  /** Which topic `db` the cached payload belongs to. */
  readonly #loadedForDb = signal<string | null>(null);
  /** Avoid duplicate in-flight requests when routes remount mid-fetch. */
  #fetchingDb: string | null = null;

  /**
   * Load events for *db* when not already cached. Safe to call on every mount —
   * cache hits return immediately with no loading flash and no HTTP round-trip.
   */
  load(db: string): void {
    if (this.#loadedForDb() === db && this.payload()) {
      return;
    }
    if (this.#fetchingDb === db) {
      return;
    }

    this.#fetchingDb = db;
    this.loading.set(true);
    this.error.set(null);

    const url = `/api/${db}/events?t=${Date.now()}`;
    this.#http.get<EventsPayload>(url).subscribe({
      next: (data) => {
        const events = data.events.map((ev) => normalizeResearchEvent(ev));
        this.payload.set({ ...data, events });
        this.#loadedForDb.set(db);
        this.loading.set(false);
        this.#fetchingDb = null;
      },
      error: () => {
        this.error.set(
          `Could not load events for topic database "${db}". ` +
            'Run the research pipeline and ensure MONGODB_URI is set and `python -m agent api` is running.',
        );
        this.loading.set(false);
        this.#fetchingDb = null;
      },
    });
  }

  /** Drop the cache so the next `load` refetches (e.g. after a topic switch). */
  invalidate(): void {
    this.#loadedForDb.set(null);
    this.payload.set(null);
    this.error.set(null);
  }
}
