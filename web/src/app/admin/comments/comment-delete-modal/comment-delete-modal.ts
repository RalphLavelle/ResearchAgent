import { HttpClient } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

/** Confirm and delete one visitor comment. */
@Component({
  selector: 'app-comment-delete-modal',
  templateUrl: './comment-delete-modal.html',
  styleUrl: './comment-delete-modal.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(document:keydown.escape)': 'onEscape($event)',
  },
})
export class CommentDeleteModalComponent {
  readonly db = input.required<string>();
  readonly commentId = input.required<string>();
  readonly commentName = input.required<string>();
  readonly commentPreview = input.required<string>();

  readonly closed = output<void>();
  readonly deleted = output<void>();

  protected readonly deleting = signal(false);
  protected readonly error = signal<string | null>(null);

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);

  protected onBackdropClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).classList.contains('modal-backdrop')) {
      this.closed.emit();
    }
  }

  protected onEscape(event: Event): void {
    event.preventDefault();
    this.closed.emit();
  }

  protected confirmDelete(): void {
    if (this.deleting()) {
      return;
    }

    this.deleting.set(true);
    this.error.set(null);
    const url = `/api/${this.db()}/comments/${this.commentId()}`;

    this.#http
      .delete<{ deleted: boolean }>(url)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: () => {
          this.deleting.set(false);
          this.deleted.emit();
        },
        error: (err) => {
          const message =
            err?.error?.error ?? 'Could not delete this comment. Try again.';
          this.error.set(String(message));
          this.deleting.set(false);
        },
      });
  }
}
