import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { RouterLink } from '@angular/router';

import { AdminAuthService } from '../admin-auth.service';

/** Response from ``POST /api/admin/run-targeted``. */
interface RunTargetedResponse {
  ok: boolean;
  message?: string;
  query?: string;
}

type RunTriggerStatus = 'idle' | 'running' | 'completed' | 'error';

/** Admin home — links to backend management pages plus targeted search runs. */
@Component({
  selector: 'app-admin-index',
  imports: [RouterLink, FormsModule],
  templateUrl: './admin-index.html',
  styleUrl: './admin-index.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AdminIndexComponent {
  /** Bound to the targeted-search input. */
  protected readonly searchInput = signal('');
  protected readonly runStatus = signal<RunTriggerStatus>('idle');
  protected readonly runMessage = signal<string | null>(null);

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  readonly #adminAuth = inject(AdminAuthService);

  protected isRunBusy(): boolean {
    return this.runStatus() === 'running';
  }

  /** Start a full pipeline run with one DuckDuckGo search phrase. */
  protected onTargetedSearchSubmit(event: Event): void {
    event.preventDefault();
    if (this.isRunBusy()) {
      return;
    }

    const query = this.searchInput().trim();
    if (!query) {
      this.runStatus.set('error');
      this.runMessage.set('Enter a search phrase first.');
      return;
    }

    const password = this.#adminAuth.getStoredPassword();
    if (!password) {
      this.runStatus.set('error');
      this.runMessage.set('Admin session expired — sign in again.');
      return;
    }

    this.runStatus.set('running');
    this.runMessage.set(null);

    this.#http
      .post<RunTargetedResponse>('/api/admin/run-targeted', { password, query })
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          this.runStatus.set('completed');
          const base = data.message?.trim() || 'Targeted run completed.';
          this.runMessage.set(
            data.query ? `${base} (search: “${data.query}”)` : base,
          );
        },
        error: (err) => {
          this.runStatus.set('error');
          this.runMessage.set(
            String(err?.error?.error ?? 'Could not run the targeted search.'),
          );
        },
      });
  }
}
