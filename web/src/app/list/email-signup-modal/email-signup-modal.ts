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

/** Response from ``POST /api/<db>/users/subscribe``. */
interface SubscribeResponse {
  email: string;
  subscribed_at: string;
}

/** Pop-up form for weekly email signup. */
@Component({
  selector: 'app-email-signup-modal',
  imports: [ReactiveFormsModule],
  templateUrl: './email-signup-modal.html',
  styleUrl: './email-signup-modal.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(document:keydown.escape)': 'onEscape($event)',
  },
})
export class EmailSignupModalComponent {
  readonly db = input.required<string>();

  readonly closed = output<void>();

  protected readonly submitting = signal(false);
  protected readonly error = signal<string | null>(null);
  /** When true, the thank-you panel replaces the form. */
  protected readonly submitted = signal(false);

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  readonly #fb = inject(FormBuilder);

  /** Reactive form — email field with built-in format validation. */
  protected readonly form = this.#fb.nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
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

  protected onEmailInput(): void {
    this.error.set(null);
  }

  protected submit(): void {
    this.form.markAllAsTouched();
    if (this.form.invalid) {
      return;
    }

    this.submitting.set(true);
    this.error.set(null);

    const email = this.form.controls.email.value.trim();
    const url = `/api/${this.db()}/users/subscribe`;

    this.#http
      .post<SubscribeResponse>(url, { email })
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: () => {
          this.submitting.set(false);
          this.submitted.set(true);
        },
        error: (err) => {
          const message =
            err?.error?.error ??
            'Could not save your email. Please try again in a moment.';
          this.error.set(String(message));
          this.submitting.set(false);
        },
      });
  }
}
