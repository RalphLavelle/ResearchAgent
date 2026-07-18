/** One AI step on the About page — LLM-only, end-user friendly. */
export interface PipelineStep {
  id: string;
  order: number;
  emoji: string;
  title: string;
  /** Short line shown in the collapsed panel header. */
  summary: string;
  /** Longer explanation paragraphs shown when expanded. */
  details: readonly string[];
  /** Optional PNG diagram under ``web/public/about/``. */
  diagramSrc?: string;
  diagramAlt?: string;
}

/**
 * The parts of Gigsorooni that actually talk to a large language model.
 * Everything else (crawlers, dedupe rules, poster downloads) stays off this page.
 */
export const AI_PIPELINE_STEPS: readonly PipelineStep[] = [
  {
    id: 'plan',
    order: 1,
    emoji: '🧠',
    title: 'The planner — search-query brainstormer',
    summary:
      'Every hour or so, an LLM invents fresh DuckDuckGo searches so we don’t keep asking the internet the same question.',
    details: [
      'Picture a music nerd with a whiteboard and too much coffee. That’s the planner LLM. It reads the topic (live music around Brisbane and the Gold Coast), peeks at what we searched last week, and returns a new batch of queries — gigs, concerts, “what’s on in Fortitude Valley”, and other angles we might not have tried yet.',
      'It also gets a little help from a shortlist of venue-specific questions (“What’s on at {venue}?”) so favourite rooms don’t get forgotten. The planner runs at a random temperature each time — a deliberate sprinkle of chaos so the searches don’t get stale.',
      'No planner, no fresh angles. The rest of the pipeline would be stuck re-reading the same corners of the web.',
    ],
    diagramSrc: '/about/step-planner.png',
    diagramAlt: 'Diagram: topic prompts flow into the planner LLM and out as search query strings',
  },
  {
    id: 'curate',
    order: 2,
    emoji: '✨',
    title: 'The curator — gig archaeologist',
    summary:
      'Websites are messy. This LLM digs through the rubble and hands back tidy rows: act, venue, date, poster hints.',
    details: [
      'Venue pages were built for humans scrolling on phones, not for robots with clipboards. Dates hide in paragraphs. One URL might list twelve gigs and a pie menu. The curator LLM is the patient friend who actually reads all of it and returns structured events the site can store.',
      'When the crawled text includes image markers, it can nominate a poster per gig — so you might see a real flyer instead of the venue’s logo. (Plain code handles the boring sorting and date filtering afterwards; you’re spared the lecture.)',
      'This is the heaviest lift in the AI roster: turning noisy web text into the list you scroll on the home page.',
    ],
    diagramSrc: '/about/step-curator.png',
    diagramAlt: 'Diagram: messy web text flows through the curator LLM into structured event rows',
  },
  {
    id: 'tags',
    order: 3,
    emoji: '🏷️',
    title: 'The tagger — genre sticker machine',
    summary:
      'New gigs get up to three filter tags (jazz, rock, free, …) so you can tap a pill instead of reading every line.',
    details: [
      'After events land in the database, a tagging LLM looks at untagged rows and picks short labels from tags we already use — no inventing “prog-steampunk” unless we’ve actually seen it before.',
      'That’s why the tag bar on the home page stays useful: it reflects real genres and vibes from the listings, not whatever the model felt like hallucinating that Tuesday.',
    ],
  },
  {
    id: 'dedupe',
    order: 4,
    emoji: '🕵️',
    title: 'The duplicate referee',
    summary:
      'When two listings describe the same gig with different wording, an LLM clusters them and one row gets sent to the bench.',
    details: [
      '“Dead of Winter @ Mo’s” and “Dead of Winter Festival Band Comp @ Burleigh” might be the same night out. Obvious duplicates are merged by plain rules; sneaky look-alikes get a second opinion from an LLM that reads same-day events side by side.',
      'You can also trigger this pass from Admin → Reports if duplicates piled up while the model was on a coffee break.',
    ],
  },
];
