import { Routes } from '@angular/router';

import { AboutComponent } from './about/about';
import { HomeComponent } from './home/home';
import { ReportsComponent } from './reports/reports';

export const routes: Routes = [
  { path: '', component: HomeComponent, title: 'Upcoming Live Music Events' },
  { path: 'reports', component: ReportsComponent, title: 'Show reports' },
  { path: 'about', component: AboutComponent, title: 'About this site' },
  { path: '**', redirectTo: '' },
];
