import { Routes } from '@angular/router';

import { AboutComponent } from './about/about';
import { adminAuthGuard } from './admin/admin-auth.guard';
import { AdminLoginComponent } from './admin/admin-login/admin-login';
import { AdminIndexComponent } from './admin/admin-index/admin-index';
import { AdminUsersComponent } from './admin/users/users';
import { AdminVenuesComponent } from './admin/venues/venues';
import { HomeComponent } from './home/home';
import { ReportsComponent } from './reports/reports';

export const routes: Routes = [
  { path: '', component: HomeComponent, title: 'Gigsorooni — Upcoming gigs' },
  {
    path: 'tags/:tagSlug',
    component: HomeComponent,
    title: 'Gigsorooni — Filter by tag',
  },
  {
    path: 'venues/:venueSlug',
    component: HomeComponent,
    title: 'Gigsorooni — Filter by venue',
  },
  { path: 'admin/login', component: AdminLoginComponent, title: 'Admin login' },
  {
    path: 'admin',
    component: AdminIndexComponent,
    title: 'Admin',
    canActivate: [adminAuthGuard],
  },
  {
    path: 'admin/reports',
    component: ReportsComponent,
    title: 'Pipeline reports',
    canActivate: [adminAuthGuard],
  },
  {
    path: 'admin/venues',
    component: AdminVenuesComponent,
    title: 'Venues',
    canActivate: [adminAuthGuard],
  },
  {
    path: 'admin/users',
    component: AdminUsersComponent,
    title: 'Users',
    canActivate: [adminAuthGuard],
  },
  { path: 'reports', redirectTo: 'admin/reports', pathMatch: 'full' },
  { path: 'about', component: AboutComponent, title: 'About Gigsorooni' },
  { path: '**', redirectTo: '' },
];
