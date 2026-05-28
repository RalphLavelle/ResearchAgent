import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';

/** Admin home — links to backend management pages. */
@Component({
  selector: 'app-admin-index',
  imports: [RouterLink],
  templateUrl: './admin-index.html',
  styleUrl: './admin-index.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AdminIndexComponent {}
