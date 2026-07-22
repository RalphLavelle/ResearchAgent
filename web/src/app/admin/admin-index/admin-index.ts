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

/** Response from ``POST /api/admin/run-once``. */
interface RunOnceResponse {
  ok: boolean;
  message?: string;
}

/** Response from ``POST /api/admin/dedupe-events``. */
interface DedupeResponse {
  ok: boolean;
  message?: string;
  removed_deterministic?: number;
  removed_semantic?: number;
  total_removed?: number;
}

type RunTriggerStatus = 'idle' | 'running' | 'completed' | 'error';

/** Admin home — pipeline actions, targeted search, and links to management pages. */
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
  protected readonly targetedRunStatus = signal<RunTriggerStatus>('idle');
  protected readonly targetedRunMessage = signal<string | null>(null);
  /** Full pipeline run triggered from the admin home page. */
  protected readonly pipelineRunStatus = signal<RunTriggerStatus>('idle');
  protected readonly pipelineRunMessage = signal<string | null>(null);
  /** Manual dedupe remediation — re-scan MongoDB without a full pipeline run. */
  protected readonly dedupeStatus = signal<RunTriggerStatus>('idle');
  protected readonly dedupeMessage = signal<string | null>(null);

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  readonly #adminAuth = inject(AdminAuthService);

  /** True while any pipeline-related action is running. */
  protected isAnyPipelineBusy(): boolean {
    return (
      this.targetedRunStatus() === 'running' ||
      this.pipelineRunStatus() === 'running' ||
      this.dedupeStatus() === 'running'
    );
  }

  /** True while the full pipeline run button should stay disabled. */
  protected isPipelineRunBusy(): boolean {
    return this.isAnyPipelineBusy();
  }

  /** True while the dedupe button should stay disabled. */
  protected isDedupeBusy(): boolean {
    return this.isAnyPipelineBusy();
  }

  /** Start a full pipeline run with one DuckDuckGo search phrase. */
  protected onTargetedSearchSubmit(event: Event): void {
    event.preventDefault();
    if (this.isAnyPipelineBusy()) {
      return;
    }

    const query = this.searchInput().trim();
    if (!query) {
      this.targetedRunStatus.set('error');
      this.targetedRunMessage.set('Enter a search phrase first.');
      return;
    }

    const password = this.#adminAuth.getStoredPassword();
    if (!password) {
      this.targetedRunStatus.set('error');
      this.targetedRunMessage.set('Admin session expired — sign in again.');
      return;
    }

    this.targetedRunStatus.set('running');
    this.targetedRunMessage.set(null);

    this.#http
      .post<RunTargetedResponse>('/api/admin/run-targeted', { password, query })
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          this.targetedRunStatus.set('completed');
          const base = data.message?.trim() || 'Targeted run completed.';
          this.targetedRunMessage.set(
            data.query ? `${base} (search: “${data.query}”)` : base,
          );
        },
        error: (err) => {
          this.targetedRunStatus.set('error');
          this.targetedRunMessage.set(
            String(err?.error?.error ?? 'Could not run the targeted search.'),
          );
        },
      });
  }

  /** Start a single research pipeline pass via the admin API. */
  protected triggerRunOnce(): void {
    if (this.isPipelineRunBusy()) {
      return;
    }

    const password = this.#adminAuth.getStoredPassword();
    if (!password) {
      this.pipelineRunStatus.set('error');
      this.pipelineRunMessage.set('Admin session expired — sign in again from Admin.');
      return;
    }

    this.pipelineRunStatus.set('running');
    this.pipelineRunMessage.set(null);

    this.#http
      .post<RunOnceResponse>('/api/admin/run-once', { password })
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          this.pipelineRunStatus.set('completed');
          this.pipelineRunMessage.set(data.message?.trim() || 'Pipeline run completed.');
        },
        error: (err) => {
          this.pipelineRunStatus.set('error');
          this.pipelineRunMessage.set(
            String(err?.error?.error ?? 'Could not run the research pipeline.'),
          );
        },
      });
  }

  /** Re-scan the events collection for duplicates (deterministic + LLM when available). */
  protected triggerDedupe(): void {
    if (this.isDedupeBusy()) {
      return;
    }

    const password = this.#adminAuth.getStoredPassword();
    if (!password) {
      this.dedupeStatus.set('error');
      this.dedupeMessage.set('Admin session expired — sign in again from Admin.');
      return;
    }

    this.dedupeStatus.set('running');
    this.dedupeMessage.set(null);

    this.#http
      .post<DedupeResponse>('/api/admin/dedupe-events', { password })
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          this.dedupeStatus.set('completed');
          this.dedupeMessage.set(data.message?.trim() || 'Dedupe scan completed.');
        },
        error: (err) => {
          this.dedupeStatus.set('error');
          this.dedupeMessage.set(
            String(err?.error?.error ?? 'Could not run duplicate removal.'),
          );
        },
      });
  }
}
