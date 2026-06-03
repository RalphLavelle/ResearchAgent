import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { AdminAuthService } from './admin-auth.service';

/** Block admin pages until the session password has been verified with the API. */
export const adminAuthGuard: CanActivateFn = async (_route, state) => {
  const auth = inject(AdminAuthService);
  const router = inject(Router);
  const stored = auth.getStoredPassword();

  if (!stored) {
    return router.createUrlTree(['/admin/login'], {
      queryParams: { returnUrl: state.url },
    });
  }

  const ok = await firstValueFrom(auth.verifyPassword(stored));
  if (!ok.ok) {
    auth.clearAccess();
    return router.createUrlTree(['/admin/login'], {
      queryParams: { returnUrl: state.url },
    });
  }

  return true;
};
