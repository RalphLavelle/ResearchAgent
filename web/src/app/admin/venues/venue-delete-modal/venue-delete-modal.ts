import { HttpClient } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { VenueRecord, VenuesPayload } from '../venues';

/** Venue detail for delete confirmation. */
interface VenueDetail {
  _id: string;
  name: string;
  aliases: string[];
  linkedEventCount: number;
}

/** Delete a venue after reassigning linked events to another venue. */
@Component({
  selector: 'app-venue-delete-modal',
  templateUrl: './venue-delete-modal.html',
  styleUrl: './venue-delete-modal.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(document:keydown.escape)': 'onEscape($event)',
  },
})
export class VenueDeleteModalComponent {
  readonly db = input.required<string>();
  readonly venueId = input.required<string>();
  readonly venueName = input.required<string>();

  readonly closed = output<void>();
  readonly deleted = output<void>();

  protected readonly loading = signal(true);
  protected readonly deleting = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly linkedEventCount = signal(0);
  protected readonly replacementOptions = signal<VenueRecord[]>([]);
  protected readonly selectedReplacementId = signal('');

  protected readonly canConfirm = computed(
    () => !!this.selectedReplacementId() && !this.deleting()
  );

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);

  constructor() {
    effect(() => {
      this.#loadDeleteContext(this.db(), this.venueId());
    });
  }

  protected onBackdropClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).classList.contains('modal-backdrop')) {
      this.closed.emit();
    }
  }

  protected onEscape(event: Event): void {
    event.preventDefault();
    this.closed.emit();
  }

  protected onReplacementChange(event: Event): void {
    this.selectedReplacementId.set((event.target as HTMLSelectElement).value);
  }

  protected confirmDelete(): void {
    const replacementVenueId = this.selectedReplacementId();
    if (!replacementVenueId) {
      return;
    }

    this.deleting.set(true);
    this.error.set(null);
    const url = `/api/${this.db()}/venues/${this.venueId()}`;

    this.#http
      .delete<{ events_updated: number; venues_deleted: number }>(url, {
        body: { replacementVenueId },
      })
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: () => {
          this.deleting.set(false);
          this.deleted.emit();
        },
        error: (err) => {
          const message =
            err?.error?.error ?? 'Could not delete venue. Try again.';
          this.error.set(String(message));
          this.deleting.set(false);
        },
      });
  }

  #loadDeleteContext(db: string, venueId: string): void {
    this.loading.set(true);
    this.error.set(null);
    this.selectedReplacementId.set('');

    const detailUrl = `/api/${db}/venues/${venueId}?t=${Date.now()}`;
    const listUrl = `/api/${db}/venues?all=true&t=${Date.now()}`;

    this.#http
      .get<VenueDetail>(detailUrl)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (detail) => {
          this.linkedEventCount.set(detail.linkedEventCount ?? 0);
          this.#loadReplacementOptions(listUrl, venueId);
        },
        error: () => {
          this.error.set('Could not load venue details.');
          this.loading.set(false);
        },
      });
  }

  #loadReplacementOptions(listUrl: string, venueId: string): void {
    this.#http
      .get<VenuesPayload>(listUrl)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          const options = (data.venues ?? []).filter((venue) => venue.id !== venueId);
          this.replacementOptions.set(options);
          if (options.length === 1) {
            this.selectedReplacementId.set(options[0].id);
          }
          if (!options.length) {
            this.error.set('No other venues exist to reassign linked events to.');
          }
          this.loading.set(false);
        },
        error: () => {
          this.error.set('Could not load replacement venue list.');
          this.loading.set(false);
        },
      });
  }
}
