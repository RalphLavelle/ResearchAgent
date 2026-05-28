import { Routes } from '@angular/router';

import { AboutComponent } from './about/about';
import { AdminIndexComponent } from './admin/admin-index/admin-index';
import { AdminVenuesComponent } from './admin/venues/venues';
import { HomeComponent } from './home/home';
import { ReportsComponent } from './reports/reports';

export const routes: Routes = [
  { path: '', component: HomeComponent, title: 'Upcoming Live Music Events' },
  { path: 'admin', component: AdminIndexComponent, title: 'Admin' },
  { path: 'admin/reports', component: ReportsComponent, title: 'Pipeline reports' },
  { path: 'admin/venues', component: AdminVenuesComponent, title: 'Venues' },
  { path: 'reports', redirectTo: 'admin/reports', pathMatch: 'full' },
  { path: 'about', component: AboutComponent, title: 'About this site' },
  { path: '**', redirectTo: '' },
];
