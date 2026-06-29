import { ChangeDetectionStrategy, Component } from '@angular/core';
import { NgOptimizedImage } from '@angular/common';
import { RouterLink } from '@angular/router';

import { SpotlightCarouselComponent } from '../spotlight-carousel/spotlight-carousel';
import { PIPELINE_STEPS, PipelineStep } from './pipeline-steps';

/**
 * About page: visual walkthrough of how the research agent uses LLMs.
 * Each pipeline step sits in a native <details> panel (collapsed by default).
 */
@Component({
  selector: 'app-about',
  imports: [RouterLink, NgOptimizedImage, SpotlightCarouselComponent],
  templateUrl: './about.html',
  styleUrl: './about.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AboutComponent {
  /** Ordered LangGraph steps — only plan and curate call an LLM. */
  protected readonly steps: readonly PipelineStep[] = PIPELINE_STEPS;

  /** Badge label for step headers. */
  protected stepBadge(step: PipelineStep): string {
    return step.usesLlm ? 'Uses AI' : 'Plain code';
  }
}
