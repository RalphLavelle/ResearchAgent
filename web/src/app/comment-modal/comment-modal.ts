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
import {
  FormBuilder,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';

/** Response from ``POST /api/<db>/comments``. */
interface CommentResponse {
  name: string;
  comment: string;
  date: string;
}

/** Pop-up form for visitor comments and suggestions. */
@Component({
  selector: 'app-comment-modal',
  imports: [ReactiveFormsModule],
  templateUrl: './comment-modal.html',
  styleUrl: './comment-modal.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(document:keydown.escape)': 'onEscape($event)',
  },
})
export class CommentModalComponent {
  readonly db = input.required<string>();

  readonly closed = output<void>();

  protected readonly submitting = signal(false);
  protected readonly error = signal<string | null>(null);
  /** When true, the thank-you panel replaces the form. */
  protected readonly submitted = signal(false);

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  readonly #fb = inject(FormBuilder);

  /** Reactive form — name plus comment textarea. */
  protected readonly form = this.#fb.nonNullable.group({
    name: ['', [Validators.required, Validators.maxLength(100)]],
    comment: ['', [Validators.required, Validators.maxLength(2000)]],
  });

  protected onBackdropClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).classList.contains('modal-backdrop')) {
      this.closed.emit();
    }
  }

  protected onEscape(event: Event): void {
    event.preventDefault();
    this.closed.emit();
  }

  protected onInput(): void {
    this.error.set(null);
  }

  protected submit(): void {
    this.form.markAllAsTouched();
    if (this.form.invalid) {
      return;
    }

    this.submitting.set(true);
    this.error.set(null);

    const name = this.form.controls.name.value.trim();
    const comment = this.form.controls.comment.value.trim();
    const url = `/api/${this.db()}/comments`;

    this.#http
      .post<CommentResponse>(url, { name, comment })
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: () => {
          this.submitting.set(false);
          this.submitted.set(true);
        },
        error: (err) => {
          const message =
            err?.error?.error ??
            'Could not save your comment. Please try again in a moment.';
          this.error.set(String(message));
          this.submitting.set(false);
        },
      });
  }
}
