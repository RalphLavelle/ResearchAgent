import { HttpClient } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

/** Response from ``GET /api/<db>/events/<id>/youtube``. */
interface YouTubePayload {
  videoId: string;
  title: string;
  cached?: boolean;
}

/** Modal that loads and embeds a YouTube clip for one event. */
@Component({
  selector: 'app-youtube-modal',
  templateUrl: './youtube-modal.html',
  styleUrl: './youtube-modal.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(document:keydown.escape)': 'onEscape($event)',
  },
})
export class YouTubeModalComponent implements OnInit {
  readonly db = input.required<string>();
  readonly eventId = input.required<string>();
  readonly eventName = input.required<string>();

  readonly closed = output<void>();

  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly videoTitle = signal('');

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  readonly #sanitizer = inject(DomSanitizer);

  /** Trusted embed URL once the API returns a video id. */
  protected readonly embedUrl = signal<SafeResourceUrl | null>(null);

  /** Accessible label for the iframe — includes the event name. */
  protected readonly iframeTitle = computed(
    () => `YouTube video for ${this.eventName()}`,
  );

  /** Inputs are bound after construction — load the clip once they are ready. */
  ngOnInit(): void {
    this.#loadVideo();
  }

  protected onBackdropClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).classList.contains('modal-backdrop')) {
      this.closed.emit();
    }
  }

  protected onEscape(event: Event): void {
    event.preventDefault();
    this.closed.emit();
  }

  /** Build a safe YouTube embed URL from a video id. */
  #embedFor(videoId: string): SafeResourceUrl {
    const url = `https://www.youtube.com/embed/${encodeURIComponent(videoId)}?autoplay=1&rel=0`;
    return this.#sanitizer.bypassSecurityTrustResourceUrl(url);
  }

  #loadVideo(): void {
    const db = this.db();
    const eventId = this.eventId();
    if (!db || !eventId) {
      this.loading.set(false);
      this.error.set('Could not load this video.');
      return;
    }

    this.#http
      .get<YouTubePayload>(`/api/${db}/events/${eventId}/youtube`)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          const videoId = data.videoId?.trim();
          if (!videoId) {
            this.error.set('No matching YouTube video was found.');
            this.loading.set(false);
            return;
          }
          this.videoTitle.set(data.title?.trim() || this.eventName());
          this.embedUrl.set(this.#embedFor(videoId));
          this.loading.set(false);
        },
        error: (err) => {
          const message =
            err?.error?.error ??
            'Could not load a YouTube video for this act right now.';
          this.error.set(String(message));
          this.loading.set(false);
        },
      });
  }
}
