import { HttpClient } from '@angular/common/http';
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

/** Raw venue document returned by ``GET /api/<db>/venues/<id>``. */
interface VenueDocument {
  _id: string;
  name: string;
  aliases: string[];
  linkedEventCount?: number;
}

/** Edit a venue as raw JSON and save with PUT. */
@Component({
  selector: 'app-venue-edit-modal',
  templateUrl: './venue-edit-modal.html',
  styleUrl: './venue-edit-modal.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(document:keydown.escape)': 'onEscape($event)',
  },
})
export class VenueEditModalComponent {
  readonly db = input.required<string>();
  readonly venueId = input.required<string>();

  readonly closed = output<void>();
  readonly saved = output<void>();

  protected readonly jsonText = signal('');
  protected readonly loading = signal(true);
  protected readonly saving = signal(false);
  protected readonly error = signal<string | null>(null);

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);

  constructor() {
    effect(() => {
      this.#loadVenue(this.db(), this.venueId());
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

  protected onJsonInput(event: Event): void {
    this.jsonText.set((event.target as HTMLTextAreaElement).value);
  }

  protected save(): void {
    this.saving.set(true);
    this.error.set(null);

    let parsed: unknown;
    try {
      parsed = JSON.parse(this.jsonText());
    } catch {
      this.error.set('Invalid JSON — check commas, quotes, and brackets.');
      this.saving.set(false);
      return;
    }

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      this.error.set('Venue document must be a JSON object.');
      this.saving.set(false);
      return;
    }

    const url = `/api/${this.db()}/venues/${this.venueId()}`;
    this.#http
      .put<VenueDocument>(url, parsed)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: () => {
          this.saving.set(false);
          this.saved.emit();
        },
        error: (err) => {
          const message =
            err?.error?.error ??
            'Could not save venue. Check the JSON and try again.';
          this.error.set(String(message));
          this.saving.set(false);
        },
      });
  }

  #loadVenue(db: string, venueId: string): void {
    this.loading.set(true);
    this.error.set(null);
    const url = `/api/${db}/venues/${venueId}?t=${Date.now()}`;

    this.#http
      .get<VenueDocument>(url)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (doc) => {
          const { linkedEventCount: _ignored, ...editable } = doc;
          this.jsonText.set(JSON.stringify(editable, null, 2));
          this.loading.set(false);
        },
        error: () => {
          this.error.set('Could not load venue document.');
          this.loading.set(false);
        },
      });
  }
}
