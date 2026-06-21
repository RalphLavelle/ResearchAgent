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
import { RouterLink } from '@angular/router';

import { TopicService } from '../../topic/topic.service';
import {
  VenueDeleteModalComponent,
  VenueDeleteOutcome,
} from './venue-delete-modal/venue-delete-modal';
import { VenueEditModalComponent } from './venue-edit-modal/venue-edit-modal';

/** One venue from ``GET /api/<db>/venues``. */
export interface VenueRecord {
  id: string;
  name: string;
  aliases: string[];
  location: string;
  /** Discovered "What's On" page for the venue (venue-first mining, Task 1). */
  events_link?: string;
  /** ISO date of the latest event seen for this venue. */
  last_event_date?: string;
}

/** Root JSON shape from the venues API. */
export interface VenuesPayload {
  venues: VenueRecord[];
  total: number;
  limit: number;
  skip: number;
}

/** Max venues shown per page (matches API cap). */
const PAGE_SIZE = 50;

@Component({
  selector: 'app-admin-venues',
  imports: [RouterLink, VenueEditModalComponent, VenueDeleteModalComponent],
  templateUrl: './venues.html',
  styleUrl: './venues.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AdminVenuesComponent {
  protected readonly venues = signal<VenueRecord[]>([]);
  protected readonly total = signal(0);
  protected readonly skip = signal(0);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly actionMessage = signal<string | null>(null);
  protected readonly editVenue = signal<VenueRecord | null>(null);
  protected readonly deleteVenue = signal<VenueRecord | null>(null);

  protected readonly pageSize = PAGE_SIZE;

  protected readonly pageNumber = computed(() => Math.floor(this.skip() / PAGE_SIZE) + 1);
  protected readonly totalPages = computed(() =>
    Math.max(1, Math.ceil(this.total() / PAGE_SIZE))
  );
  protected readonly hasPrevious = computed(() => this.skip() > 0);
  protected readonly hasNext = computed(() => this.skip() + PAGE_SIZE < this.total());
  protected readonly rangeStart = computed(() => (this.total() === 0 ? 0 : this.skip() + 1));
  protected readonly rangeEnd = computed(() =>
    Math.min(this.skip() + this.venues().length, this.total())
  );

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  protected readonly topic = inject(TopicService);
  readonly #reloadTick = signal(0);

  constructor() {
    effect(() => {
      const db = this.topic.active().db;
      const skip = this.skip();
      this.#reloadTick();
      if (!this.topic.loading()) {
        this.#loadVenues(db, skip);
      }
    });
  }

  /** Join aliases for compact table display. */
  protected aliasesLabel(aliases: string[]): string {
    if (!aliases.length) {
      return '—';
    }
    return aliases.join(', ');
  }

  /** Short host label for a venue's "What's On" link (e.g. ``thetriffid.com.au``). */
  protected eventsLinkHost(url: string | undefined): string {
    if (!url) {
      return '';
    }
    try {
      return new URL(url).host.replace(/^www\./, '');
    } catch {
      return url;
    }
  }

  protected openEdit(venue: VenueRecord): void {
    this.actionMessage.set(null);
    this.editVenue.set(venue);
  }

  protected closeEdit(): void {
    this.editVenue.set(null);
  }

  protected onVenueSaved(): void {
    this.editVenue.set(null);
    this.actionMessage.set('Venue saved.');
    this.#refreshList();
  }

  protected openDelete(venue: VenueRecord): void {
    this.actionMessage.set(null);
    this.deleteVenue.set(venue);
  }

  protected closeDelete(): void {
    this.deleteVenue.set(null);
  }

  protected onVenueDeleted(outcome: VenueDeleteOutcome): void {
    this.deleteVenue.set(null);
    const messages: Record<VenueDeleteOutcome, string> = {
      none: 'Venue deleted.',
      reassign: 'Venue deleted and linked events reassigned.',
      delete: 'Venue deleted and linked events removed.',
    };
    this.actionMessage.set(messages[outcome]);
    this.#refreshList();
  }

  protected goToPreviousPage(): void {
    if (!this.hasPrevious()) {
      return;
    }
    this.skip.update((current) => Math.max(0, current - PAGE_SIZE));
  }

  protected goToNextPage(): void {
    if (!this.hasNext()) {
      return;
    }
    this.skip.update((current) => current + PAGE_SIZE);
  }

  #refreshList(): void {
    this.#reloadTick.update((count) => count + 1);
  }

  #loadVenues(db: string, skip: number): void {
    this.loading.set(true);
    this.error.set(null);
    const url =
      `/api/${db}/venues?limit=${PAGE_SIZE}&skip=${skip}&t=${Date.now()}`;

    this.#http
      .get<VenuesPayload>(url)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          this.venues.set(data.venues ?? []);
          this.total.set(data.total ?? 0);
          this.loading.set(false);
        },
        error: () => {
          this.error.set(
            `Could not load venues for topic database "${db}". ` +
              'Ensure `python -m agent api` is running and MongoDB is reachable.'
          );
          this.loading.set(false);
        },
      });
  }
}
