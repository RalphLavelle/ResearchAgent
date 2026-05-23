import { ChangeDetectionStrategy, Component, inject } from '@angular/core';

import { ListComponent } from '../list/list';
import { TopicService } from '../topic/topic.service';

/**
 * Home route: hero text plus the events list for the active topic.
 */
@Component({
  selector: 'app-home',
  imports: [ListComponent],
  templateUrl: './home.html',
  styleUrl: './home.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class HomeComponent {
  protected readonly topic = inject(TopicService);
}
