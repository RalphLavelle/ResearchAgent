import { Routes } from '@angular/router';

import { AboutComponent } from './about/about';
import { adminAuthGuard } from './admin/admin-auth.guard';
import { AdminLoginComponent } from './admin/admin-login/admin-login';
import { AdminIndexComponent } from './admin/admin-index/admin-index';
import { AdminUsersComponent } from './admin/users/users';
import { AdminCommentsComponent } from './admin/comments/comments';
import { AdminVenuesComponent } from './admin/venues/venues';
import { HomeComponent } from './home/home';
import { NotFoundComponent } from './not-found/not-found';
import { ReportsComponent } from './reports/reports';

// Routes with `data: { noindex: true }` get a robots noindex meta tag from
// the SeoService — admin pages and the 404 page should never be indexed.
export const routes: Routes = [
  {
    path: '',
    component: HomeComponent,
    title: 'Gigsorooni — Live music gigs in Brisbane & the Gold Coast',
  },
  {
    path: 'tags/:tagSlug',
    component: HomeComponent,
    // Refined to "<tag> gigs — Gigsorooni" by ListComponent once loaded.
    title: 'Gigsorooni — Gigs by tag',
  },
  {
    path: 'venues/:venueSlug',
    component: HomeComponent,
    // Refined to "<venue> gigs — Gigsorooni" by ListComponent once loaded.
    title: 'Gigsorooni — Gigs by venue',
  },
  {
    path: 'admin/login',
    component: AdminLoginComponent,
    title: 'Admin login',
    data: { noindex: true },
  },
  {
    path: 'admin',
    component: AdminIndexComponent,
    title: 'Admin',
    canActivate: [adminAuthGuard],
    data: { noindex: true },
  },
  {
    path: 'admin/reports',
    component: ReportsComponent,
    title: 'Pipeline reports',
    canActivate: [adminAuthGuard],
    data: { noindex: true },
  },
  {
    path: 'admin/venues',
    component: AdminVenuesComponent,
    title: 'Venues',
    canActivate: [adminAuthGuard],
    data: { noindex: true },
  },
  {
    path: 'admin/users',
    component: AdminUsersComponent,
    title: 'Users',
    canActivate: [adminAuthGuard],
    data: { noindex: true },
  },
  {
    path: 'admin/comments',
    component: AdminCommentsComponent,
    title: 'Comments',
    canActivate: [adminAuthGuard],
    data: { noindex: true },
  },
  { path: 'reports', redirectTo: 'admin/reports', pathMatch: 'full' },
  { path: 'about', component: AboutComponent, title: 'About Gigsorooni' },
  {
    path: '**',
    component: NotFoundComponent,
    title: 'Page not found — Gigsorooni',
    data: { noindex: true },
  },
];
