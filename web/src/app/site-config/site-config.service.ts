import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { take } from 'rxjs/operators';

/** Response from ``GET /api/config``. */
interface SiteConfigResponse {
  emailSignupEnabled: boolean;
  googleAnalyticsMeasurementId?: string | null;
}

/**
 * Loads public UI flags from the API (backed by ``.env`` on the server).
 * Defaults to disabled when the config endpoint is unavailable.
 */
@Injectable({ providedIn: 'root' })
export class SiteConfigService {
  readonly #http = inject(HttpClient);

  readonly #config = signal<SiteConfigResponse | null>(null);

  /** When true, show the weekly email signup button on the home page. */
  readonly emailSignupEnabled = computed(
    () => this.#config()?.emailSignupEnabled ?? false,
  );

  /** GA4 Measurement ID from ``GOOGLE_ANALYTICS_MEASUREMENT_ID``; null when analytics is disabled. */
  readonly googleAnalyticsMeasurementId = computed(() => {
    const id = this.#config()?.googleAnalyticsMeasurementId?.trim();
    return id ? id : null;
  });

  constructor() {
    this.#http
      .get<SiteConfigResponse>('/api/config')
      .pipe(take(1))
      .subscribe({
        next: (data) => this.#config.set(data),
        error: () => this.#config.set({ emailSignupEnabled: false }),
      });
  }
}
