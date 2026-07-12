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
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { ResearchEvent, normalizeResearchEvent, posterSrc } from '../events/research-event.model';
import { EventsStore } from '../events/events-store.service';
import { TopicService } from '../topic/topic.service';
import { EmailSignupModalComponent } from './email-signup-modal/email-signup-modal';
import {
  slugify,
  venueFilterKey,
  venueFilterKeyForSlug,
} from './event-filter-slug';
import { SpotlightCarouselComponent } from '../spotlight-carousel/spotlight-carousel';

/** Response from ``POST /api/<db>/events/search``. */
interface SearchPayload {
  generated: string;
  events: ResearchEvent[];
  searchQuery: string;
}

@Component({
  selector: 'app-list',
  imports: [
    NgOptimizedImage,
    RouterLink,
    FormsModule,
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
  readonly #http = inject(HttpClient);

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
  /** Bound to the AI search input — updated as the user types. */
  protected readonly searchInput = signal('');
  /** Active search term after the user presses Enter (synced to ``?search=``). */
  protected readonly activeSearchQuery = signal<string | null>(null);
  /** LLM-filtered events when a search is active; null before the first search. */
  protected readonly searchResults = signal<ResearchEvent[] | null>(null);
  protected readonly searchLoading = signal(false);
  protected readonly searchError = signal<string | null>(null);

  /** Active topic MongoDB name — passed to the signup modal API call. */
  protected readonly activeDb = computed(() => this.#topic.active().db);

  /** Venue slug from the URL until events load and we can resolve the filter key. */
  readonly #pendingVenueSlug = signal<string | null>(null);
  /** Search term from ``?search=`` waiting for the active topic database to be ready. */
  readonly #pendingSearchTerm = signal<string | null>(null);

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

  /** Events after optional venue, tag, and AI search filters are applied. */
  protected readonly visibleEvents = computed(() => {
    const searchActive = this.activeSearchQuery();
    const searchRows = this.searchResults();
    const data = this.payload();
    if (!data) {
      return [] as ResearchEvent[];
    }

    const baseEvents =
      searchActive && searchRows !== null
        ? searchRows
        : data.events;

    const venueKey = this.activeVenueFilterKey();
    const tag = this.activeTagFilter();
    return baseEvents.filter((ev) => {
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

    this.#route.queryParamMap
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe((params) => {
        const term = (params.get('search') ?? '').trim();
        if (!term) {
          this.#clearSearchState();
          return;
        }

        // Search spans all events — drop tag/venue path segments if present.
        const tagSlug = this.#route.snapshot.paramMap.get('tagSlug');
        const venueSlug = this.#route.snapshot.paramMap.get('venueSlug');
        if (tagSlug || venueSlug) {
          void this.#router.navigate(['/'], {
            queryParams: { search: term },
            replaceUrl: true,
          });
          return;
        }

        this.searchInput.set(term);
        this.activeSearchQuery.set(term);
        this.#pendingSearchTerm.set(term);
      });

    effect(() => {
      const term = this.#pendingSearchTerm();
      const db = this.#topic.active().db;
      if (!term || !db || this.#topic.loading()) {
        return;
      }
      this.#pendingSearchTerm.set(null);
      this.#runSearch(term);
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

  /** Run AI search when the user presses Enter in the search bar. */
  protected onSearchSubmit(event: Event): void {
    event.preventDefault();
    const term = this.searchInput().trim();
    if (!term) {
      void this.#router.navigate(['/'], {
        queryParams: { search: null },
      });
      return;
    }
    // Leave tag/venue routes — search applies to the full event list.
    void this.#router.navigate(['/'], {
      queryParams: { search: term },
    });
  }

  /** Clear the active AI search and remove ``?search`` from the URL. */
  protected clearSearch(): void {
    this.searchInput.set('');
    void this.#router.navigate([], {
      relativeTo: this.#route,
      queryParams: { search: null },
      queryParamsHandling: 'merge',
    });
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

  #clearSearchState(): void {
    this.#pendingSearchTerm.set(null);
    this.activeSearchQuery.set(null);
    this.searchResults.set(null);
    this.searchLoading.set(false);
    this.searchError.set(null);
    if (!this.#route.snapshot.queryParamMap.get('search')) {
      this.searchInput.set('');
    }
  }

  #runSearch(term: string): void {
    const db = this.#topic.active().db;
    if (!db) {
      return;
    }

    this.activeSearchQuery.set(term);
    this.searchLoading.set(true);
    this.searchError.set(null);
    this.searchResults.set(null);

    this.#http
      .post<SearchPayload>(`/api/${db}/events/search`, { query: term })
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          const events = (data.events ?? []).map((ev) =>
            normalizeResearchEvent(ev as ResearchEvent),
          );
          this.searchResults.set(events);
          this.searchLoading.set(false);
        },
        error: (err) => {
          this.searchError.set(
            String(err?.error?.error ?? 'AI search could not complete — try again shortly.'),
          );
          this.searchLoading.set(false);
        },
      });
  }
}
