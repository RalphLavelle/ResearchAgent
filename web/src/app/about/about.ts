import { ChangeDetectionStrategy, Component } from '@angular/core';
import { NgOptimizedImage } from '@angular/common';
import { RouterLink } from '@angular/router';

import { SpotlightCarouselComponent } from '../spotlight-carousel/spotlight-carousel';
import { AI_PIPELINE_STEPS } from './pipeline-steps';

/**
 * About page: fun, end-user-focused tour of where Gigsorooni uses LLMs.
 * Non-AI pipeline steps are intentionally omitted.
 */
@Component({
  selector: 'app-about',
  imports: [RouterLink, NgOptimizedImage, SpotlightCarouselComponent],
  templateUrl: './about.html',
  styleUrl: './about.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AboutComponent {
  /** LLM-only steps — planner, curator, tagger, duplicate referee. */
  protected readonly steps = AI_PIPELINE_STEPS;
}
