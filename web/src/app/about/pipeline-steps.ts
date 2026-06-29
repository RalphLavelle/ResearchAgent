/** One step in the LangGraph research pipeline — used by the about page accordion. */
export interface PipelineStep {
  /** Stable id for aria-controls and tracking. */
  id: string;
  /** Display order (1-based). */
  order: number;
  emoji: string;
  title: string;
  /** True when this step invokes an LLM. */
  usesLlm: boolean;
  /** Short line shown in the collapsed panel header. */
  summary: string;
  /** Longer explanation paragraphs shown when expanded. */
  details: readonly string[];
  /** Optional PNG diagram under ``web/public/about/``. */
  diagramSrc?: string;
  diagramAlt?: string;
}

/**
 * Pipeline steps in run order.
 * Only **plan** and **curate** call an LLM; everything else is deterministic code.
 */
export const PIPELINE_STEPS: readonly PipelineStep[] = [
  {
    id: 'plan',
    order: 1,
    emoji: '🧠',
    title: 'Plan — invent search queries',
    usesLlm: true,
    summary:
      'An LLM reads topic prompts and recent searches, then returns fresh DuckDuckGo query strings.',
    details: [
      'Every hour or so the agent wakes up and asks a planner LLM: “What should we search for next?” The model knows the topic (for example live music around Brisbane and the Gold Coast) and returns varied queries — gigs, concerts, “what’s on in Fortitude Valley”, and similar angles.',
      'The planner also sees recent searches from past runs so it does not repeat itself. A handful of targeted venue queries (“What’s on in {venue} in {location}”) are merged in ahead of the LLM’s list.',
      'If the LLM is unavailable, the run can still continue when targeted venue queries exist; otherwise the pipeline stops early with a diagnostic note.',
    ],
    diagramSrc: '/about/step-planner.png',
    diagramAlt: 'Diagram: topic prompts flow into the planner LLM and out as search query strings',
  },
  {
    id: 'search',
    order: 2,
    emoji: '🔍',
    title: 'Search — DuckDuckGo snippets',
    usesLlm: false,
    summary: 'Plain code runs each planned query through DuckDuckGo and collects raw text snippets.',
    details: [
      'No AI here — just LangChain’s DuckDuckGo tool fetching result snippets for every query the planner produced.',
      'The combined blob of text is passed to later steps. Search does not need an API key.',
    ],
  },
  {
    id: 'crawl',
    order: 3,
    emoji: '🕷️',
    title: 'Crawl — follow promising venue pages',
    usesLlm: false,
    summary:
      'Deterministic crawlers fetch listing pages — especially venue “What’s On” URLs — and append HTML text.',
    details: [
      'Search snippets alone often miss gigs buried on paginated venue calendars. The crawl step follows internal links with a bounded page budget, prioritising ticket and event URLs over menus and shop pages.',
      'Known venues discovered in search results get their “What’s On” link mined first. Seeds are crawled round-robin so no single site hogs the whole budget.',
    ],
  },
  {
    id: 'curate',
    order: 4,
    emoji: '✨',
    title: 'Curate — extract structured gigs',
    usesLlm: true,
    summary:
      'The curator LLM reads messy search + crawl text and returns structured rows: act, venue, date, poster hints.',
    details: [
      'Websites are not built for machines — layouts change, dates sit in prose, and one page may list dozens of gigs. The curator LLM is good at turning that noise into a structured list the app can store.',
      'It may also pick a poster image per event when inline [IMG …] markers are present in the crawled text. After the LLM returns, plain code filters out past dates, sorts soonest-first, and dedupes identical rows.',
    ],
    diagramSrc: '/about/step-curator.png',
    diagramAlt: 'Diagram: messy web text flows through the curator LLM into structured event rows',
  },
  {
    id: 'enrich',
    order: 5,
    emoji: '🖼️',
    title: 'Enrich — poster images',
    usesLlm: false,
    summary: 'Code fills missing thumbnails using page Open Graph images and filename scoring.',
    details: [
      'When the curator leaves a poster slot blank, enrich fetches each event page’s og:image. A scoring pass prefers filenames and alt text that match the act name.',
      'Posters are downloaded once per upstream URL and cached in MongoDB — many events can share one cached image.',
    ],
  },
  {
    id: 'fingerprint',
    order: 6,
    emoji: '🔑',
    title: 'Fingerprint — detect changes',
    usesLlm: false,
    summary: 'A hash of the curated list is compared to the last run so unchanged runs skip heavy writes.',
    details: [
      'This quick checksum step avoids rewriting MongoDB when nothing new was found. The snapshot lives on disk under data/<topic_id>/snapshot.json.',
    ],
  },
  {
    id: 'output',
    order: 7,
    emoji: '💾',
    title: 'Save — dedupe and publish',
    usesLlm: false,
    summary:
      'Deterministic merge rules dedupe gigs, cache posters, and write events plus a run report to MongoDB.',
    details: [
      'Duplicate detection is **not** done by an LLM. Code merges rows when the same act and date appear on different sites, appends extra URLs to Sources, and drops past events.',
      'The Angular app reads the result through GET /api/<db>/events. Admin reports show searches, crawled URLs, and merge stats for each run.',
    ],
  },
];
