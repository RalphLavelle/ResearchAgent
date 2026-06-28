import { NgOptimizedImage } from '@angular/common';
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
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { ResearchEvent, posterSrc } from '../events/research-event.model';
import { EventsStore } from '../events/events-store.service';
import { TopicService } from '../topic/topic.service';
import { EmailSignupModalComponent } from './email-signup-modal/email-signup-modal';
import {
  slugify,
  venueFilterKey,
  venueFilterKeyForSlug,
} from './event-filter-slug';
import { SpotlightCarouselComponent } from '../spotlight-carousel/spotlight-carousel';

@Component({
  selector: 'app-list',
  imports: [
    NgOptimizedImage,
    RouterLink,
    EmailSignupModalComponent,
    SpotlightCarouselComponent,
  ],
  templateUrl: './list.html',
  styleUrl: './list.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ListComponent {
  /** Shared slug helpers exposed for the template. */
  protected readonly venueFilterKey = venueFilterKey;

  readonly #destroyRef = inject(DestroyRef);
  readonly #events = inject(EventsStore);
  readonly #topic = inject(TopicService);
  readonly #route = inject(ActivatedRoute);
  readonly #router = inject(Router);

  /** Cached events snapshot — shared across route remounts (see EventsStore). */
  protected readonly payload = this.#events.payload;
  protected readonly loading = this.#events.loading;
  protected readonly error = this.#events.error;

  /**
   * Defensive fallback: event IDs whose poster failed to load even though the
   * pipeline cached it in MongoDB. Tracking failures lets the template swap in
   * the 🎸 placeholder instead of a broken-image icon.
   */
  protected readonly posterErrors = signal<ReadonlySet<string>>(new Set());
  /** When set, only events for this venue filter key are shown. */
  protected readonly activeVenueFilterKey = signal<string | null>(null);
  /** When set, only events containing this tag are shown. */
  protected readonly activeTagFilter = signal<string | null>(null);
  /** When true, the weekly email signup modal is open. */
  protected readonly emailSignupOpen = signal(false);

  /** Active topic MongoDB name — passed to the signup modal API call. */
  protected readonly activeDb = computed(() => this.#topic.active().db);

  /** Venue slug from the URL until events load and we can resolve the filter key. */
  readonly #pendingVenueSlug = signal<string | null>(null);

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
      if (venueKey && venueFilterKey(ev) !== venueKey) {
        return false;
      }
      if (tag && !(ev.tags ?? []).includes(tag)) {
        return false;
      }
      return true;
    });
  });

  constructor() {
    this.#route.paramMap.pipe(takeUntilDestroyed(this.#destroyRef)).subscribe((params) => {
      this.#syncFiltersFromRoute(
        params.get('tagSlug'),
        params.get('venueSlug'),
      );
    });

    effect(() => {
      const db = this.#topic.active().db;
      if (!this.#topic.loading()) {
        this.#events.load(db);
      }
    });

    effect(() => {
      const data = this.payload();
      const slug = this.#pendingVenueSlug();
      if (!data || !slug) {
        return;
      }
      this.activeVenueFilterKey.set(venueFilterKeyForSlug(data.events, slug));
    });
  }

  protected posterSrc(url: string | null): string | null {
    return posterSrc(url);
  }

  /** URL slug for a venue name (bookmarkable ``/venues/...`` segment). */
  protected venueSlug(ev: ResearchEvent): string {
    return slugify(ev.venue);
  }

  protected toggleVenueFilter(ev: ResearchEvent): void {
    const key = venueFilterKey(ev);
    if (!key) {
      return;
    }
    if (this.activeVenueFilterKey() === key) {
      void this.#router.navigateByUrl('/');
      return;
    }
    void this.#router.navigate(['/venues', slugify(ev.venue)]);
  }

  protected toggleTagFilter(tag: string): void {
    const key = tag.trim().toLowerCase();
    if (!key) {
      return;
    }
    if (this.activeTagFilter() === key) {
      void this.#router.navigateByUrl('/');
      return;
    }
    void this.#router.navigate(['/tags', key]);
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

  /** Apply tag or venue filter from the current route (bookmarkable URLs). */
  #syncFiltersFromRoute(tagSlug: string | null, venueSlugParam: string | null): void {
    if (tagSlug) {
      this.activeTagFilter.set(decodeURIComponent(tagSlug).trim().toLowerCase());
      this.activeVenueFilterKey.set(null);
      this.#pendingVenueSlug.set(null);
      return;
    }
    if (venueSlugParam) {
      this.activeTagFilter.set(null);
      this.#pendingVenueSlug.set(decodeURIComponent(venueSlugParam).trim());
      const data = this.payload();
      if (data) {
        this.activeVenueFilterKey.set(
          venueFilterKeyForSlug(data.events, this.#pendingVenueSlug() ?? ''),
        );
      } else {
        this.activeVenueFilterKey.set(null);
      }
      return;
    }
    this.activeTagFilter.set(null);
    this.activeVenueFilterKey.set(null);
    this.#pendingVenueSlug.set(null);
  }
}
