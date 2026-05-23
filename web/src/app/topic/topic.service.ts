import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { take } from 'rxjs/operators';

/** One topic entry from ``topics/topics.json``. */
export interface TopicEntry {
  name: string;
  data_dir: string;
  background_image: string;
  site_title: string;
  site_emoji: string;
  home_heading: string;
}

/** Root shape of ``topics/topics.json``. */
export interface TopicsRegistry {
  active: string;
  topics: Record<string, TopicEntry>;
}

/**
 * Loads the active topic from ``/topics/topics.json`` (copied from repo ``topics/``).
 * Drives background image, site chrome, and the events JSON subfolder path.
 */
@Injectable({ providedIn: 'root' })
export class TopicService {
  readonly #http = inject(HttpClient);

  /** Full registry once loaded; null until the first fetch completes. */
  readonly #registry = signal<TopicsRegistry | null>(null);
  readonly #loadError = signal<string | null>(null);

  /** True while topics.json is in flight. */
  readonly loading = signal(true);

  readonly loadError = this.#loadError.asReadonly();

  /** Active topic id from the registry (falls back before load). */
  readonly activeId = computed(() => this.#registry()?.active ?? '');

  /** Active topic entry, or sensible defaults when the file is missing. */
  readonly active = computed<TopicEntry>(() => {
    const reg = this.#registry();
    if (!reg) {
      return {
        name: 'Events',
        data_dir: 'live-music-brisbane-gold-coast',
        background_image: '/topics/live-music-brisbane-gold-coast/assets/bg.jpg',
        site_title: 'Live music events',
        site_emoji: '🎵',
        home_heading: "What's on",
      };
    }
    return reg.topics[reg.active];
  });

  /** Relative URL for ``events.json`` under the topic data subfolder. */
  readonly eventsJsonUrl = computed(
    () => `data/${this.active().data_dir}/events.json`,
  );

  constructor() {
    this.#http
      .get<TopicsRegistry>('topics/topics.json?t=' + Date.now())
      .pipe(take(1))
      .subscribe({
        next: (data) => {
          this.#registry.set(data);
          this.loading.set(false);
        },
        error: () => {
          this.#loadError.set('Could not load topics/topics.json.');
          this.loading.set(false);
        },
      });
  }
}
