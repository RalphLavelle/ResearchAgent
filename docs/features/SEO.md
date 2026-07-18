# SEO report — Gigsorooni

Task 16 (`docs/ideas/04. SEO`): *"Plan a series of improvements to the site to improve its SEO. Carry out each step…"*

The site was a client-rendered Angular SPA with a bare HTML shell: no meta description, no social tags, no robots.txt or sitemap, no structured data, no `<h1>`, and unknown URLs silently redirected to the home page. Everything below is now implemented and verified (287 Python tests pass, Angular production build succeeds).

## What was done

### 1. Meta tags in `web/src/index.html`

Crawlers and social scrapers that don't run JavaScript now see a real page description:

- Descriptive `<title>` ("Gigsorooni — Live music gigs in Brisbane & the Gold Coast")
- `meta description`, `theme-color`
- Open Graph tags (`og:type`, `og:site_name`, `og:title`, `og:description`, `og:image`) and Twitter Card tags

### 2. `robots.txt` and `sitemap.xml` — generated live by the API

Rather than static files, the Python API now serves both (`src/agent/seo.py`, routes in `src/agent/api.py`), and nginx proxies the site-root paths to it (`deploy/nginx.conf`; dev proxying in `web/proxy.conf.json`):

- **`/robots.txt`** — allows all crawlers, blocks `/admin`, and links the sitemap.
- **`/sitemap.xml`** — lists `/`, `/about`, plus one URL for every distinct tag (`/tags/rock`) and venue (`/venues/the-triffid`) currently in the display window. It regenerates on each request, so it always matches the live event data.
- URLs are built from the request's `Host` / `X-Forwarded-Proto` headers — **no domain is hard-coded**, so this works unchanged on localhost, the DigitalOcean default domain, and any future custom domain.
- The Python `slugify` mirrors the Angular one exactly (verified by tests) so sitemap URLs resolve in the SPA.

### 3. `SeoService` (`web/src/app/seo/seo.service.ts`)

One service now owns the document head. On every navigation it:

- points the **canonical link** at the clean URL (origin + path, query params stripped — so `/?search=jazz` canonicalises to `/`),
- keeps **`og:url`** in sync,
- applies **`noindex, nofollow`** to routes marked `data: { noindex: true }` — all `/admin` pages and the 404 page,
- resets the **meta description** to the active topic's default.

### 4. Slug-specific titles and descriptions for filter pages

Tag and venue routes had generic titles ("Gigsorooni — Filter by tag"). The list component now sets real ones once data loads: **"rock gigs — Gigsorooni"**, **"The Triffid gigs — Gigsorooni"**, each with a matching description. These are the long-tail search pages ("what's on at the Triffid"), so they matter most.

### 5. schema.org structured data (JSON-LD)

The list page publishes an `ItemList` of **`MusicEvent`** objects (name, ISO start date, venue as `MusicVenue`, summary, poster image, ticket URL) — the markup Google uses for event rich results. To support it, the events API now includes a machine-readable **`isoDate`** alongside the display date (`src/agent/json_output.py`).

### 6. Heading structure and an accessibility fix

- The home page had **no `<h1>`**. It now renders one from the topic's new `tagline` ("Live music gigs in Brisbane & the Gold Coast"), styled with the existing gradient panel-title look.
- This also fixes a broken ARIA reference: the events section declared `aria-labelledby="events-heading"` but no such element existed. The new h1 carries that id.
- Topic copy lives in `topics/topics.json` (new `tagline` + `description` fields per topic), keeping the engine topic-agnostic — the Galway topic got its own copy too.

### 7. Real 404 page

Unknown URLs used to redirect to the home page — a "soft 404" that wastes crawl budget and pollutes the index. There is now a proper `NotFoundComponent` (title "Page not found", `noindex`, friendly link back to the gigs).

## Files changed

| Area | Files |
|------|-------|
| Static meta | `web/src/index.html` |
| Head management | `web/src/app/seo/seo.service.ts` (new), `web/src/app/app.ts` |
| Routes / 404 / noindex | `web/src/app/app.routes.ts`, `web/src/app/not-found/not-found.ts` (new) |
| Listing page | `web/src/app/list/list.ts`, `list.html`, `list.css` |
| Topic copy | `topics/topics.json`, `web/src/app/topic/topic.service.ts` |
| API `isoDate` | `src/agent/json_output.py`, `web/src/app/events/research-event.model.ts` |
| robots/sitemap | `src/agent/seo.py` (new), `src/agent/api.py`, `deploy/nginx.conf`, `web/proxy.conf.json` |
| Tests | `tests/test_seo.py` (new) |
| Docs | `README.md` (new endpoints) |

## What to do next (not code — operational)

1. **Set up a custom domain** and register the site in [Google Search Console](https://search.google.com/search-console) and Bing Webmaster Tools; submit `/sitemap.xml` there. This is the single biggest discoverability lever.
2. **`og:image` is currently the logotype at a relative path.** Social scrapers want an absolute URL and a wide (1200×630) image — once the domain is fixed, consider a dedicated share image and an absolute `og:image` URL in `index.html`.
3. **Backlinks** — being listed on venue pages, local gig-guide directories, and community Facebook groups will do more than any on-page change.

## Possible future code work

- **Prerendering / SSR** (`@angular/ssr`): the biggest remaining on-page gap. Event content is still rendered client-side; Googlebot executes JS so it will index it, but prerendering `/`, `/about`, and top tag pages would make first-crawl indexing faster and social previews richer. It's a significant architectural change (server or build-time rendering), so it was deliberately left out of this pass.
- **Per-event detail pages** (`/events/<slug>`) would unlock long-tail queries like "the beths brisbane tickets", at the cost of new routes and API support.
