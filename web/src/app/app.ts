import { ChangeDetectionStrategy, Component, signal } from '@angular/core';

import { ListComponent } from './list/list';

@Component({
  selector: 'app-root',
  imports: [ListComponent],
  templateUrl: './app.html',
  styleUrl: './app.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class App {
  /** Page title shown above the embedded research HTML. */
  protected readonly title = signal('Upcoming Live Music Events');
}
