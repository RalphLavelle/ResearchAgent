import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { catchError, map, Observable, of } from 'rxjs';

/** Result of a password check against the API. */
export interface AdminVerifyResult {
  ok: boolean;
  error?: string;
}

/** sessionStorage key set after a successful admin login. */
export const ADMIN_PASSWORD_STORAGE_KEY = 'enteredPassword';

/**
 * Tracks whether the browser session has passed the admin password gate.
 * The password itself is stored in sessionStorage per the product spec.
 */
@Injectable({ providedIn: 'root' })
export class AdminAuthService {
  readonly #http = inject(HttpClient);

  /** Return the password saved for this tab session, if any. */
  getStoredPassword(): string | null {
    if (typeof sessionStorage === 'undefined') {
      return null;
    }
    const value = sessionStorage.getItem(ADMIN_PASSWORD_STORAGE_KEY);
    return value?.trim() ? value : null;
  }

  /** Persist a verified password for the rest of the browser session. */
  storePassword(password: string): void {
    sessionStorage.setItem(ADMIN_PASSWORD_STORAGE_KEY, password);
  }

  /** Clear the saved password (e.g. after a failed re-check). */
  clearAccess(): void {
    sessionStorage.removeItem(ADMIN_PASSWORD_STORAGE_KEY);
  }

  /** Ask the API whether *password* matches ``ADMIN_PASSWORD`` in ``.env``. */
  verifyPassword(password: string): Observable<AdminVerifyResult> {
    return this.#http.post<{ ok: boolean }>('/api/admin/verify-password', { password }).pipe(
      map(() => ({ ok: true })),
      catchError((err) =>
        of({
          ok: false,
          error: String(err?.error?.error ?? 'Could not verify password.'),
        }),
      ),
    );
  }
}
