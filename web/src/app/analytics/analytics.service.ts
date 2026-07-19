import { DOCUMENT } from '@angular/common';
import { Injectable, effect, inject } from '@angular/core';
import { NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs/operators';

import { SiteConfigService } from '../site-config/site-config.service';

/** Minimal gtag.js surface used by this service. */
type GtagFn = (...args: unknown[]) => void;

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: GtagFn;
  }
}

/**
 * Loads Google Analytics 4 via the official gtag.js script and records SPA page views.
 *
 * The Measurement ID comes from ``GET /api/config`` (``GOOGLE_ANALYTICS_MEASUREMENT_ID`` in
 * ``.env``). When unset, this service is a no-op — no script, no errors.
 */
@Injectable({ providedIn: 'root' })
export class AnalyticsService {
  readonly #document = inject(DOCUMENT);
  readonly #router = inject(Router);
  readonly #siteConfig = inject(SiteConfigService);

  /** Avoid sending the same page view twice for one navigation. */
  #lastTrackedUrl: string | null = null;
  #measurementId: string | null = null;
  #initialized = false;

  constructor() {
    effect(() => {
      const measurementId = this.#siteConfig.googleAnalyticsMeasurementId();
      if (!measurementId || this.#initialized) {
        return;
      }
      this.#initialize(measurementId);
    });

    this.#router.events
      .pipe(filter((event): event is NavigationEnd => event instanceof NavigationEnd))
      .subscribe((event) => this.#trackPageView(event.urlAfterRedirects));
  }

  /** Inject gtag.js, configure GA4, and record the current route. */
  #initialize(measurementId: string): void {
    this.#measurementId = measurementId;
    this.#ensureGtagBootstrap();
    this.#loadGtagScript(measurementId);

    window.gtag?.('js', new Date());
    window.gtag?.('config', measurementId, { send_page_view: false });

    this.#initialized = true;
    this.#trackPageView(this.#router.url);
  }

  /** Create ``dataLayer`` and the local ``gtag`` shim before the remote script loads. */
  #ensureGtagBootstrap(): void {
    window.dataLayer = window.dataLayer ?? [];
    if (!window.gtag) {
      window.gtag = (...args: unknown[]) => {
        window.dataLayer?.push(args);
      };
    }
  }

  /** Append the official async gtag.js loader for the configured Measurement ID. */
  #loadGtagScript(measurementId: string): void {
    const existing = this.#document.getElementById('ga-gtag-script');
    if (existing) {
      return;
    }

    const script = this.#document.createElement('script');
    script.id = 'ga-gtag-script';
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
    this.#document.head.appendChild(script);
  }

  /** Send a GA4 ``page_view`` for the given router URL (path + query string). */
  #trackPageView(url: string): void {
    if (!this.#initialized || !this.#measurementId || !window.gtag) {
      return;
    }

    const pagePath = url || '/';
    if (pagePath === this.#lastTrackedUrl) {
      return;
    }

    this.#lastTrackedUrl = pagePath;

    const origin = this.#document.location?.origin ?? '';
    window.gtag('event', 'page_view', {
      send_to: this.#measurementId,
      page_path: pagePath,
      page_location: `${origin}${pagePath}`,
      page_title: this.#document.title,
    });
  }
}
