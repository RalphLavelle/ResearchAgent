import { Routes } from '@angular/router';

import { AboutComponent } from './about/about';
import { HomeComponent } from './home/home';

export const routes: Routes = [
  { path: '', component: HomeComponent, title: 'Upcoming Live Music Events' },
  { path: 'about', component: AboutComponent, title: 'About this site' },
  { path: '**', redirectTo: '' },
];
