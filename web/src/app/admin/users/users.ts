import { DatePipe } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';

import { TopicService } from '../../topic/topic.service';

/** One weekly-email subscriber from ``GET /api/<db>/users``. */
export interface UserRecord {
  id: string;
  email: string;
  subscribed_at: string;
}

/** Root JSON shape from the users API. */
export interface UsersPayload {
  users: UserRecord[];
  total: number;
  limit: number;
  skip: number;
}

/** Max subscribers shown per page (matches API cap). */
const PAGE_SIZE = 50;

@Component({
  selector: 'app-admin-users',
  imports: [DatePipe, RouterLink],
  templateUrl: './users.html',
  styleUrl: './users.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AdminUsersComponent {
  protected readonly users = signal<UserRecord[]>([]);
  protected readonly total = signal(0);
  protected readonly skip = signal(0);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  protected readonly pageSize = PAGE_SIZE;

  protected readonly pageNumber = computed(() => Math.floor(this.skip() / PAGE_SIZE) + 1);
  protected readonly totalPages = computed(() =>
    Math.max(1, Math.ceil(this.total() / PAGE_SIZE))
  );
  protected readonly hasPrevious = computed(() => this.skip() > 0);
  protected readonly hasNext = computed(() => this.skip() + PAGE_SIZE < this.total());
  protected readonly rangeStart = computed(() => (this.total() === 0 ? 0 : this.skip() + 1));
  protected readonly rangeEnd = computed(() =>
    Math.min(this.skip() + this.users().length, this.total())
  );

  readonly #http = inject(HttpClient);
  readonly #destroyRef = inject(DestroyRef);
  protected readonly topic = inject(TopicService);

  constructor() {
    effect(() => {
      const db = this.topic.active().db;
      const skip = this.skip();
      if (!this.topic.loading()) {
        this.#loadUsers(db, skip);
      }
    });
  }

  protected goToPreviousPage(): void {
    if (!this.hasPrevious()) {
      return;
    }
    this.skip.update((current) => Math.max(0, current - PAGE_SIZE));
  }

  protected goToNextPage(): void {
    if (!this.hasNext()) {
      return;
    }
    this.skip.update((current) => current + PAGE_SIZE);
  }

  #loadUsers(db: string, skip: number): void {
    this.loading.set(true);
    this.error.set(null);
    const url = `/api/${db}/users?limit=${PAGE_SIZE}&skip=${skip}&t=${Date.now()}`;

    this.#http
      .get<UsersPayload>(url)
      .pipe(takeUntilDestroyed(this.#destroyRef))
      .subscribe({
        next: (data) => {
          this.users.set(data.users ?? []);
          this.total.set(data.total ?? 0);
          this.loading.set(false);
        },
        error: () => {
          this.error.set(
            `Could not load users for topic database "${db}". ` +
              'Ensure `python -m agent api` is running and MongoDB is reachable.'
          );
          this.loading.set(false);
        },
      });
  }
}
