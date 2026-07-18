import { DOCUMENT } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { NgOptimizedImage } from '@angular/common';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { SeoService } from './seo/seo.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, NgOptimizedImage],
  templateUrl: './app.html',
  styleUrl: './app.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(window:resize)': 'onViewportResize()',
    '(document:keydown.escape)': 'onEscapeCloseNav()',
  },
})
export class App {
  /** When true, the mobile slide-out nav is visible (only styled on small viewports). */
  protected readonly navOpen = signal(false);

  private readonly document = inject(DOCUMENT);
  // Instantiated here so canonical/robots/description tracking starts with the first navigation.
  private readonly seo = inject(SeoService);
  private readonly menuButton = viewChild<ElementRef<HTMLButtonElement>>('menuButton');
  private readonly firstMobileLink = viewChild<ElementRef<HTMLAnchorElement>>('firstMobileLink');

  constructor() {
    // Lock page scroll while the mobile menu is open so the list doesn’t move behind the overlay.
    effect(() => {
      const open = this.navOpen();
      this.document.body.classList.toggle('app-mobile-nav-open', open);
    });
  }

  /** Opens the mobile nav if closed, closes if open, then moves focus for keyboard users. */
  protected toggleNav(): void {
    const next = !this.navOpen();
    this.navOpen.set(next);
    if (next) {
      queueMicrotask(() => this.firstMobileLink()?.nativeElement.focus());
    } else {
      queueMicrotask(() => this.menuButton()?.nativeElement.focus());
    }
  }

  /** Dismisses the overlay when Escape is pressed (keyboard / assisted tech). */
  protected onEscapeCloseNav(): void {
    this.closeNav();
  }

  /** Clears the mobile menu when the layout switches to the wide header links. */
  protected onViewportResize(): void {
    if (
      this.document.defaultView?.matchMedia('(min-width: 48rem)').matches &&
      this.navOpen()
    ) {
      this.navOpen.set(false);
    }
  }

  /** Closes the mobile nav and returns focus to the menu button. */
  protected closeNav(): void {
    if (!this.navOpen()) {
      return;
    }
    this.navOpen.set(false);
    queueMicrotask(() => this.menuButton()?.nativeElement.focus());
  }

}
