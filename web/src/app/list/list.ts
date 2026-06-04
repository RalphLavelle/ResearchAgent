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

import {
  EventsPayload,
  ResearchEvent,
  normalizeResearchEvent,
  posterSrc,
} from '../events/research-event.model';
import { TopicService } from '../topic/topic.service';
import { EmailSignupModalComponent } from './email-signup-modal/email-signup-modal';
import { SpotlightCarouselComponent } from '../spotlight-carousel/spotlight-carousel';

@Component({
  selector: 'app-list',
  imports: [NgOptimizedImage, EmailSignupModalComponent, SpotlightCarouselComponent],
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
  /** When true, the weekly email signup modal is open. */
  protected readonly emailSignupOpen = signal(false);

  /** Active topic MongoDB name — passed to the signup modal API call. */
  protected readonly activeDb = computed(() => this.#topic.active().db);

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  readonly #topic = inject(TopicService);

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

  constructor() {
    effect(() => {
      const db = this.#topic.active().db;
      if (!this.#topic.loading()) {
        this.#loadEvents(db);
      }
    });
  }

  protected posterSrc(url: string | null): string | null {
    return posterSrc(url);
  }

  protected toggleVenueFilter(ev: ResearchEvent): void {
    const key = this.venueFilterKey(ev);
    if (!key) {
      return;
    }
    this.activeVenueFilterKey.update((current) => (current === key ? null : key));
  }

  protected toggleTagFilter(tag: string): void {
    const key = tag.trim().toLowerCase();
    if (!key) {
      return;
    }
    this.activeTagFilter.update((current) => (current === key ? null : key));
  }

  protected venueFilterKey(ev: ResearchEvent): string | null {
    const id = (ev.venueId ?? '').trim();
    if (id) {
      return `id:${id}`;
    }
    const name = ev.venue.trim().toLowerCase();
    return name ? `name:${name}` : null;
  }

  protected venueButtonLabel(ev: ResearchEvent): string {
    const parts = [ev.venue.trim(), ev.location.trim()].filter(Boolean);
    return parts.join(', ');
  }

  protected openEmailSignup(): void {
    this.emailSignupOpen.set(true);
  }

  protected closeEmailSignup(): void {
    this.emailSignupOpen.set(false);
  }

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
          const events = data.events.map((ev) => normalizeResearchEvent(ev));
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
}
