import { HttpClient } from '@angular/common/http';
import { NgOptimizedImage } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import {
  EventsPayload,
  ResearchEvent,
  featuredVenueLine,
  normalizeResearchEvent,
  posterSrc,
} from '../events/research-event.model';
import { TopicService } from '../topic/topic.service';

/** Spotlight picks carousel — four random events with cached posters from the API. */
@Component({
  selector: 'app-spotlight-carousel',
  imports: [NgOptimizedImage],
  templateUrl: './spotlight-carousel.html',
  styleUrl: './spotlight-carousel.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SpotlightCarouselComponent {
  /** When true, show the weekly email signup button beside the heading. */
  readonly showEmailSignup = input(false);

  readonly emailSignupClick = output<void>();

  protected readonly featuredEvents = signal<ResearchEvent[]>([]);
  protected readonly featuredIndex = signal(0);
  protected readonly posterErrors = signal<ReadonlySet<string>>(new Set());

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  readonly #topic = inject(TopicService);

  constructor() {
    effect(() => {
      const db = this.#topic.active().db;
      if (!this.#topic.loading()) {
        this.#loadSpotlight(db, []);
      }
    });
  }

  protected venueLine(ev: ResearchEvent): string {
    return featuredVenueLine(ev);
  }

  protected posterUrl(url: string | null): string | null {
    return posterSrc(url);
  }

  protected showCarouselNav(): boolean {
    return this.featuredEvents().length > 1;
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

  protected onPosterError(eventId: string): void {
    const failed = new Set(this.posterErrors());
    if (failed.has(eventId)) {
      return;
    }
    failed.add(eventId);
    this.posterErrors.set(failed);

    const db = this.#topic.active().db;
    const exclude = this.featuredEvents().map((ev) => ev.id);
    this.#loadSpotlight(db, exclude, { replaceFailedId: eventId });
  }

  /**
   * Load spotlight rows from MongoDB (only events with ``image_id``).
   * When a poster fails in the browser, pass failed ids in *exclude* to draw replacements.
   */
  #loadSpotlight(
    db: string,
    exclude: string[],
    options?: { replaceFailedId?: string },
  ): void {
    const excludeParam = [...new Set(exclude.filter(Boolean))].join(',');
    const url =
      `/api/${db}/events/spotlight?limit=4` +
      (excludeParam ? `&exclude=${encodeURIComponent(excludeParam)}` : '') +
      `&t=${Date.now()}`;

    this.#http
      .get<Pick<EventsPayload, 'events'>>(url)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          const incoming = (data.events ?? []).map((ev) => normalizeResearchEvent(ev));

          if (options?.replaceFailedId) {
            const kept = this.featuredEvents().filter(
              (ev) => ev.id !== options.replaceFailedId,
            );
            const used = new Set(kept.map((ev) => ev.id));
            const replacement = incoming.find((ev) => !used.has(ev.id));
            const next = replacement ? [...kept, replacement] : kept;
            this.featuredEvents.set(next.slice(0, 4));
          } else {
            this.featuredEvents.set(incoming.slice(0, 4));
            this.featuredIndex.set(0);
            this.posterErrors.set(new Set());
          }

          const count = this.featuredEvents().length;
          if (count > 0) {
            this.featuredIndex.update((index) => Math.min(index, count - 1));
          }
        },
        error: () => {
          if (!options?.replaceFailedId) {
            this.featuredEvents.set([]);
          }
        },
      });
  }
}
