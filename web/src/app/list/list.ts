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

/** One row from the topic's ``data/<topic>/events.json`` file. */
export interface ResearchEvent {
  /** Stable spreadsheet id — not displayed; use for `track` / future features. */
  id: string;
  eventName: string;
  venue: string;
  date: string;
  url: string;
  summary: string;
  thumbnailUrl: string | null;
}

/** Root JSON shape from `json_output.write_events_json`. */
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
  /** Loaded snapshot: spreadsheet-backed events + generation time. */
  protected readonly payload = signal<EventsPayload | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  /**
   * Defensive fallback (task 14): event IDs whose poster failed to load even
   * though the pipeline cached it locally. Tracking the failures lets the
   * template swap in the 🎸 placeholder instead of leaving a broken-image icon.
   */
  protected readonly posterErrors = signal<ReadonlySet<string>>(new Set());

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  readonly #topic = inject(TopicService);

  constructor() {
    // Reload when topics.json finishes and the active data_dir is known.
    effect(() => {
      const dataDir = this.#topic.active().data_dir;
      if (!this.#topic.loading()) {
        this.#loadEvents(dataDir);
      }
    });
  }

  /** User-triggered reload — uses cache-busting query param so the browser does not serve a stale asset. */
  protected refreshList(): void {
    this.posterErrors.set(new Set());
    this.#loadEvents(this.#topic.active().data_dir);
  }

  /**
   * Root-absolute poster URL for ``ngSrc`` (fixes legacy ``data/images/`` paths
   * and route-relative resolution on nested pages like ``/about``).
   */
  protected posterSrc(url: string | null): string | null {
    if (!url) {
      return null;
    }
    if (url.startsWith('http://') || url.startsWith('https://')) {
      return url;
    }
    let path = url.startsWith('/') ? url.slice(1) : url;
    const topic = this.#topic.active().data_dir;
    if (path.startsWith('data/images/')) {
      path = `data/${topic}/images/${path.slice('data/images/'.length)}`;
    }
    return `/${path}`;
  }

  /**
   * Hook for the poster `<img>`'s `(error)` event. Adding the id flips the
   * template to the placeholder branch so the row stops trying to render the
   * missing asset (task 14).
   */
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

  /** GET ``data/<topic>/events.json``; falls back to legacy flat ``data/events.json``. */
  #loadEvents(dataDir: string): void {
    this.loading.set(true);
    this.error.set(null);
    const primary = `data/${dataDir}/events.json?t=${Date.now()}`;
    const legacy = `data/events.json?t=${Date.now()}`;

    this.#http
      .get<EventsPayload>(primary)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          this.payload.set(data);
          this.loading.set(false);
        },
        error: () => {
          this.#http
            .get<EventsPayload>(legacy)
            .pipe(takeUntilDestroyed(this.#destroyRef))
            .subscribe({
              next: (data) => {
                this.payload.set(data);
                this.loading.set(false);
              },
              error: () => {
                this.error.set(
                  `Could not load events.json for topic "${dataDir}". Run the research pipeline once so data/${dataDir}/events.json exists.`
                );
                this.loading.set(false);
              },
            });
        },
      });
  }
}
