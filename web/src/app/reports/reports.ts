import { DatePipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';

import { TopicService } from '../topic/topic.service';

/** One pipeline run report from ``GET /api/<db>/reports``. */
export interface RunReport {
  id: string;
  datetime: string;
  searches: string[];
  urls: Record<string, string[]>;
  changes: Record<string, number>;
  /** Why planner/search/crawl/normalize produced no output, when applicable. */
  diagnostics?: Record<string, string>;
  /** LLM used for this run (e.g. OLLAMA_MODEL value from .env). */
  llm_model?: string;
  /** Randomised planner temperature sampled for this run (0–1 by default). */
  planner_temperature?: number;
}

/** Root JSON shape from the reports API. */
export interface ReportsPayload {
  reports: RunReport[];
}

@Component({
  selector: 'app-reports',
  imports: [DatePipe, RouterLink],
  templateUrl: './reports.html',
  styleUrl: './reports.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReportsComponent {
  protected readonly reports = signal<RunReport[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  /** Only one report row expanded at a time. */
  protected readonly expandedId = signal<string | null>(null);

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  readonly #topic = inject(TopicService);

  constructor() {
    effect(() => {
      const db = this.#topic.active().db;
      if (!this.#topic.loading()) {
        this.#loadReports(db);
      }
    });
  }

  /** Toggle expand/collapse for one report row. */
  protected toggleReport(reportId: string): void {
    this.expandedId.update((current) => (current === reportId ? null : reportId));
  }

  /** Keyboard activation for expandable rows. */
  protected onRowKeydown(event: KeyboardEvent, reportId: string): void {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      this.toggleReport(reportId);
    }
  }

  protected isExpanded(reportId: string): boolean {
    return this.expandedId() === reportId;
  }

  /** Compact cell text for the searches column. */
  protected searchesSummary(report: RunReport): string {
    if (report.searches.length) {
      if (report.searches.length === 1) {
        return report.searches[0];
      }
      return `${report.searches.length} searches`;
    }
    return this.#stepDiagnostic(report, 'planner') ?? this.#stepDiagnostic(report, 'search') ?? 'No searches';
  }

  /** Compact cell text for the urls column. */
  protected urlsSummary(report: RunReport): string {
    const hosts = Object.keys(report.urls);
    if (!hosts.length) {
      return this.#stepDiagnostic(report, 'crawl') ?? 'No URLs crawled';
    }
    const pages = hosts.reduce((sum, host) => sum + (report.urls[host]?.length ?? 0), 0);
    const hostLabel = hosts.length === 1 ? '1 host' : `${hosts.length} hosts`;
    const pageLabel = pages === 1 ? '1 page' : `${pages} pages`;
    return `${hostLabel}, ${pageLabel}`;
  }

  /** Compact cell text for the changes column. */
  protected changesSummary(changes: Record<string, number>): string {
    const keys = Object.keys(changes);
    if (!keys.length) {
      return '—';
    }
    const added = changes['added (new rows)'];
    if (typeof added === 'number') {
      return `${added} added`;
    }
    return `${keys.length} metrics`;
  }

  /** Host keys sorted for stable detail display. */
  protected urlHosts(urls: Record<string, string[]>): string[] {
    return Object.keys(urls).sort();
  }

  /** Change entries in display order. */
  protected changeEntries(changes: Record<string, number>): [string, number][] {
    return Object.entries(changes);
  }

  /** Diagnostic note for one pipeline step, if the API saved one. */
  protected stepDiagnostic(report: RunReport, step: string): string | null {
    return this.#stepDiagnostic(report, step);
  }

  /** True when the expanded report should show the diagnostics block. */
  protected hasDiagnostics(report: RunReport): boolean {
    return Object.keys(report.diagnostics ?? {}).length > 0;
  }

  protected diagnosticEntries(report: RunReport): [string, string][] {
    const labels: Record<string, string> = {
      planner: 'Planner',
      search: 'Search',
      crawl: 'Crawl',
      normalize: 'Curator',
    };
    return Object.entries(report.diagnostics ?? {}).map(([key, value]) => [
      labels[key] ?? key,
      value,
    ]);
  }

  #stepDiagnostic(report: RunReport, step: string): string | null {
    const value = report.diagnostics?.[step]?.trim();
    return value ? value : null;
  }

  #loadReports(db: string): void {
    this.loading.set(true);
    this.error.set(null);
    this.expandedId.set(null);
    const url = `/api/${db}/reports?t=${Date.now()}`;

    this.#http
      .get<ReportsPayload>(url)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          this.reports.set(data.reports ?? []);
          this.loading.set(false);
        },
        error: () => {
          this.error.set(
            `Could not load reports for topic database "${db}". ` +
              'Run the research pipeline and ensure `python -m agent api` is running.'
          );
          this.loading.set(false);
        },
      });
  }
}
