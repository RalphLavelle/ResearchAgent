import { DOCUMENT } from '@angular/common';
import { Injectable, effect, inject, signal } from '@angular/core';
import { Meta, Title } from '@angular/platform-browser';
import { NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs/operators';

import { ResearchEvent } from '../events/research-event.model';
import { TopicService } from '../topic/topic.service';

/** id of the JSON-LD <script> tag managed by this service. */
const JSON_LD_SCRIPT_ID = 'events-structured-data';

/**
 * Central SEO helper — one place that owns the document head.
 *
 * On every navigation it:
 *  - points the canonical <link> at the clean URL (origin + path, no query),
 *  - resets the meta description to the active topic's default,
 *  - applies `noindex` for routes marked with `data: { noindex: true }`
 *    (admin pages and the 404 page),
 *  - keeps the Open Graph title/url in sync with the document title.
 *
 * Components call `setListingTitle` / `setEventsJsonLd` to refine further.
 */
@Injectable({ providedIn: 'root' })
export class SeoService {
  readonly #document = inject(DOCUMENT);
  readonly #meta = inject(Meta);
  readonly #title = inject(Title);
  readonly #router = inject(Router);
  readonly #topic = inject(TopicService);

  /** True after a component overrode the description for the current route. */
  readonly #descriptionOverridden = signal(false);

  constructor() {
    this.#router.events
      .pipe(filter((ev): ev is NavigationEnd => ev instanceof NavigationEnd))
      .subscribe((ev) => this.#applyRouteDefaults(ev.urlAfterRedirects));

    // Re-apply the topic default once topics.json finishes loading
    // (the first navigation usually beats the HTTP fetch).
    effect(() => {
      const topic = this.#topic.active();
      if (!this.#descriptionOverridden() && topic.description) {
        this.#meta.updateTag({ name: 'description', content: topic.description });
        this.#meta.updateTag({ property: 'og:description', content: topic.description });
      }
    });
  }

  /** Set a route-specific title (also mirrored to Open Graph). */
  setListingTitle(title: string): void {
    this.#title.setTitle(title);
    this.#meta.updateTag({ property: 'og:title', content: title });
    this.#meta.updateTag({ name: 'twitter:title', content: title });
  }

  /** Set a route-specific description, suppressing the topic default until the next navigation. */
  setDescription(text: string): void {
    this.#descriptionOverridden.set(true);
    this.#meta.updateTag({ name: 'description', content: text });
    this.#meta.updateTag({ property: 'og:description', content: text });
  }

  /**
   * Publish schema.org MusicEvent structured data for the loaded events.
   * Google can show rich results (dates, venues) for pages carrying this.
   */
  setEventsJsonLd(events: ResearchEvent[]): void {
    const origin = this.#document.location?.origin ?? '';
    const items = events
      .filter((ev) => ev.isoDate && ev.eventName && ev.url)
      .slice(0, 100)
      .map((ev, index) => ({
        '@type': 'ListItem',
        position: index + 1,
        item: {
          '@type': 'MusicEvent',
          name: ev.eventName,
          startDate: ev.isoDate,
          url: ev.url,
          ...(ev.summary ? { description: ev.summary } : {}),
          ...(ev.thumbnailUrl
            ? { image: ev.thumbnailUrl.startsWith('http') ? ev.thumbnailUrl : origin + ev.thumbnailUrl }
            : {}),
          ...(ev.venue
            ? {
                location: {
                  '@type': 'MusicVenue',
                  name: ev.venue,
                  ...(ev.location ? { address: ev.location } : {}),
                },
              }
            : {}),
        },
      }));

    if (items.length === 0) {
      this.clearEventsJsonLd();
      return;
    }

    const payload = {
      '@context': 'https://schema.org',
      '@type': 'ItemList',
      itemListElement: items,
    };
    let script = this.#document.getElementById(JSON_LD_SCRIPT_ID) as HTMLScriptElement | null;
    if (!script) {
      script = this.#document.createElement('script');
      script.type = 'application/ld+json';
      script.id = JSON_LD_SCRIPT_ID;
      this.#document.head.appendChild(script);
    }
    script.textContent = JSON.stringify(payload);
  }

  /** Remove the structured-data script (list component destroyed). */
  clearEventsJsonLd(): void {
    this.#document.getElementById(JSON_LD_SCRIPT_ID)?.remove();
  }

  /** Canonical link, robots directive, and default description for a new route. */
  #applyRouteDefaults(url: string): void {
    this.#descriptionOverridden.set(false);

    const origin = this.#document.location?.origin ?? '';
    const path = url.split('?')[0].split('#')[0];
    this.#setCanonical(origin + path);
    this.#meta.updateTag({ property: 'og:url', content: origin + path });

    if (this.#routeHasNoindex()) {
      this.#meta.updateTag({ name: 'robots', content: 'noindex, nofollow' });
    } else {
      this.#meta.removeTag("name='robots'");
    }

    const topic = this.#topic.active();
    if (topic.description) {
      this.#meta.updateTag({ name: 'description', content: topic.description });
      this.#meta.updateTag({ property: 'og:description', content: topic.description });
    }
  }

  /** Walk the activated route tree looking for `data: { noindex: true }`. */
  #routeHasNoindex(): boolean {
    let route = this.#router.routerState.snapshot.root;
    while (route) {
      if (route.data?.['noindex']) {
        return true;
      }
      if (!route.firstChild) {
        break;
      }
      route = route.firstChild;
    }
    return false;
  }

  #setCanonical(href: string): void {
    let link = this.#document.head.querySelector<HTMLLinkElement>("link[rel='canonical']");
    if (!link) {
      link = this.#document.createElement('link');
      link.rel = 'canonical';
      this.#document.head.appendChild(link);
    }
    link.href = href;
  }
}
