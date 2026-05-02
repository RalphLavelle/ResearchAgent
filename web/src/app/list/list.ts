import { DatePipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

/** One row from `data/events.json` produced by the Python pipeline. */
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
  imports: [DatePipe],
  templateUrl: './list.html',
  styleUrl: './list.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ListComponent {
  /** Loaded snapshot: spreadsheet-backed events + generation time. */
  protected readonly payload = signal<EventsPayload | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);

  /**
   * Peers for the event-name accordion: only other rows with the same headline act in the same
   * calendar month. No fallback list of unrelated “same month” events (task 5).
   */
  protected accordionPeers(
    current: ResearchEvent,
    all: ResearchEvent[],
  ): { label: string; peers: ResearchEvent[] } {
    const month = this.#monthKeyFromDisplay(current.date);
    const headline = this.#normalizeHeadline(current.eventName);

    const sameAct = all.filter((e) => {
      if (e.id === current.id) {
        return false;
      }
      if (!month || this.#monthKeyFromDisplay(e.date) !== month) {
        return false;
      }
      return this.#normalizeHeadline(e.eventName) === headline;
    });

    if (sameAct.length > 0) {
      return { label: 'More from this act (this month)', peers: sameAct.slice(0, 12) };
    }

    // No unrelated “same month” filler — peers only when the act matches (task 5).
    return { label: '', peers: [] };
  }

  /** Last two tokens: “May 2026” — enough to group pipeline formatted dates. */
  #monthKeyFromDisplay(displayDate: string): string | null {
    const parts = displayDate.trim().split(/\s+/).filter(Boolean);
    if (parts.length < 2) {
      return null;
    }
    return `${parts[parts.length - 2]} ${parts[parts.length - 1]}`;
  }

  /** Compare headline act text before “ @ ” (spreadsheet act / event name). */
  #normalizeHeadline(name: string): string {
    const at = name.indexOf(' @ ');
    const base = at >= 0 ? name.slice(0, at) : name;
    return base.trim().toLowerCase().replace(/\s+/g, ' ');
  }

  constructor() {
    this.#loadEvents();
  }

  /** User-triggered reload — uses cache-busting query param so the browser does not serve a stale asset. */
  protected refreshList(): void {
    this.#loadEvents();
  }

  /** GET `/data/events.json` (copied from repo `data/` at build time). */
  #loadEvents(): void {
    this.loading.set(true);
    this.error.set(null);
    const url = `data/events.json?t=${Date.now()}`;
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
            'Could not load events.json. Run the research pipeline once so data/events.json exists.'
          );
          this.loading.set(false);
        },
      });
  }
}
