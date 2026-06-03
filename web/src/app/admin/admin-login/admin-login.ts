import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { AdminAuthService } from '../admin-auth.service';

/** Password gate shown before any admin route is accessible. */
@Component({
  selector: 'app-admin-login',
  imports: [ReactiveFormsModule, RouterLink],
  templateUrl: './admin-login.html',
  styleUrl: './admin-login.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AdminLoginComponent {
  protected readonly submitting = signal(false);
  protected readonly error = signal<string | null>(null);

  readonly #auth = inject(AdminAuthService);
  readonly #router = inject(Router);
  readonly #route = inject(ActivatedRoute);
  readonly #destroyRef = inject(DestroyRef);
  readonly #fb = inject(FormBuilder);

  protected readonly form = this.#fb.nonNullable.group({
    password: ['', Validators.required],
  });

  protected submit(): void {
    this.form.markAllAsTouched();
    if (this.form.invalid) {
      return;
    }

    this.submitting.set(true);
    this.error.set(null);

    const password = this.form.controls.password.value;

    this.#auth
      .verifyPassword(password)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (result) => {
          this.submitting.set(false);
          if (!result.ok) {
            this.error.set(result.error ?? 'Incorrect password — try again.');
            return;
          }
          this.#auth.storePassword(password);
          const returnUrl = this.#route.snapshot.queryParamMap.get('returnUrl') ?? '/admin';
          void this.#router.navigateByUrl(returnUrl);
        },
        error: () => {
          this.error.set('Could not verify password. Is the API running?');
          this.submitting.set(false);
        },
      });
  }
}
