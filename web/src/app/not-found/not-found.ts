import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';

/**
 * Real 404 page. Unknown URLs used to silently redirect to the home page,
 * which search engines treat as a "soft 404" — bad for crawl budget and
 * confusing for users. The route carries `data: { noindex: true }` so the
 * SeoService marks it noindex.
 */
@Component({
  selector: 'app-not-found',
  imports: [RouterLink],
  template: `
    <section class="not-found">
      <p class="not-found__emoji" aria-hidden="true">🎸🔍</p>
      <h1 class="not-found__title">Page not found</h1>
      <p class="not-found__hint">
        That page doesn't exist — but the gigs do.
        <a routerLink="/">Browse upcoming live music</a>.
      </p>
    </section>
  `,
  styles: `
    .not-found {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: var(--space-3);
      padding: var(--space-10) var(--space-4);
      text-align: center;
    }
    .not-found__emoji {
      margin: 0;
      font-size: var(--text-brand);
    }
    .not-found__title {
      margin: 0;
      font-family: var(--font-display);
      font-size: var(--text-4xl);
      color: var(--color-text);
    }
    .not-found__hint {
      margin: 0;
      max-width: var(--empty-hint-max);
      color: var(--color-muted);
    }
    .not-found__hint a {
      color: var(--color-link);
      font-weight: 600;
    }
    .not-found__hint a:hover {
      color: var(--color-link-hover);
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NotFoundComponent {}
