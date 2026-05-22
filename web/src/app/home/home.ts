import { ChangeDetectionStrategy, Component } from '@angular/core';

import { ListComponent } from '../list/list';

/**
 * Home route: hero text plus the events list loaded from `data/events.json`.
 */
@Component({
  selector: 'app-home',
  imports: [ListComponent],
  templateUrl: './home.html',
  styleUrl: './home.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class HomeComponent {}
