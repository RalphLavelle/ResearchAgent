import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';

import { SpotlightCarouselComponent } from '../spotlight-carousel/spotlight-carousel';

/**
 * Static about page: explains how LLMs power the research agent behind this UI.
 */
@Component({
  selector: 'app-about',
  imports: [RouterLink, SpotlightCarouselComponent],
  templateUrl: './about.html',
  styleUrl: './about.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AboutComponent {}
