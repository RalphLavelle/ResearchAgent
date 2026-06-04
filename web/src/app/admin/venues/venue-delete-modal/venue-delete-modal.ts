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

/** Radio choice for linked events before venue delete. */
type LinkedEventAction = 'reassign' | 'delete';

/** Outcome reported after a successful venue delete. */
export type VenueDeleteOutcome = 'none' | 'reassign' | 'delete';

/** API response after deleting a venue. */
interface VenueDeleteResponse {
  events_updated: number;
  events_deleted: number;
  venues_deleted: number;
}

/** Delete a venue; reassign or delete its linked events first. */
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
  readonly deleted = output<VenueDeleteOutcome>();

  protected readonly loading = signal(true);
  protected readonly deleting = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly linkedEventCount = signal(0);
  protected readonly replacementOptions = signal<VenueRecord[]>([]);
  protected readonly selectedReplacementId = signal('');
  protected readonly linkedEventAction = signal<LinkedEventAction>('reassign');

  protected readonly hasLinkedEvents = computed(() => this.linkedEventCount() > 0);
  protected readonly isReassignMode = computed(
    () => this.linkedEventAction() === 'reassign'
  );

  protected readonly canConfirm = computed(() => {
    if (this.deleting() || this.loading()) {
      return false;
    }
    if (!this.hasLinkedEvents()) {
      return true;
    }
    if (this.linkedEventAction() === 'delete') {
      return true;
    }
    return (
      this.replacementOptions().length > 0 && !!this.selectedReplacementId()
    );
  });

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

  protected onLinkedEventActionChange(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    if (value === 'reassign' || value === 'delete') {
      this.linkedEventAction.set(value);
      this.error.set(null);
    }
  }

  protected onReplacementChange(event: Event): void {
    this.selectedReplacementId.set((event.target as HTMLSelectElement).value);
  }

  protected confirmDelete(): void {
    if (!this.canConfirm()) {
      return;
    }

    const deleteLinkedEvents = this.hasLinkedEvents() && this.linkedEventAction() === 'delete';
    const replacementVenueId = deleteLinkedEvents
      ? undefined
      : this.selectedReplacementId() || undefined;

    if (
      this.hasLinkedEvents() &&
      !deleteLinkedEvents &&
      !replacementVenueId
    ) {
      return;
    }

    this.deleting.set(true);
    this.error.set(null);
    const url = `/api/${this.db()}/venues/${this.venueId()}`;
    const body: { deleteLinkedEvents?: boolean; replacementVenueId?: string } =
      {};
    if (deleteLinkedEvents) {
      body.deleteLinkedEvents = true;
    } else if (replacementVenueId) {
      body.replacementVenueId = replacementVenueId;
    }

    this.#http
      .delete<VenueDeleteResponse>(url, { body })
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: () => {
          this.deleting.set(false);
          let outcome: VenueDeleteOutcome = 'none';
          if (this.hasLinkedEvents()) {
            outcome = deleteLinkedEvents ? 'delete' : 'reassign';
          }
          this.deleted.emit(outcome);
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
    this.linkedEventAction.set('reassign');

    const detailUrl = `/api/${db}/venues/${venueId}?t=${Date.now()}`;
    const listUrl = `/api/${db}/venues?all=true&t=${Date.now()}`;

    this.#http
      .get<VenueDetail>(detailUrl)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (detail) => {
          this.linkedEventCount.set(detail.linkedEventCount ?? 0);
          if ((detail.linkedEventCount ?? 0) > 0) {
            this.#loadReplacementOptions(listUrl, venueId);
          } else {
            this.replacementOptions.set([]);
            this.loading.set(false);
          }
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
            this.linkedEventAction.set('delete');
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
