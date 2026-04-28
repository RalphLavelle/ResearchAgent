import { ChangeDetectionStrategy, Component } from '@angular/core';

/** Embeds the spreadsheet-generated HTML page (`data/agent_research.html`). */
@Component({
  selector: 'app-list',
  imports: [],
  templateUrl: './list.html',
  styleUrl: './list.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ListComponent {}
