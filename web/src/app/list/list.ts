import { DatePipe, NgOptimizedImage } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
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
  venue: string;
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
  imports: [DatePipe, NgOptimizedImage],
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
          this.payload.set(data);
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
}
