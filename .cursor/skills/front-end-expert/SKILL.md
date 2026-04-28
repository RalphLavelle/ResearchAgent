---
name: front-end-expert
description: Builds Node.js backends with Angular front ends using modern TypeScript, standalone components, signals, and layout-only CSS (Flexbox/Grid). Avoids third-party UI frameworks. Use when creating or refactoring Angular apps, Node APIs, component structure, or styling in this project; when the user asks for front-end work, UI, or SPA architecture.
---

# Front-end expert (Angular + Node)

## Stack

- **Backend:** Node.js (TypeScript where the project uses it) — REST or suitable API for the Angular app.
- **Front end:** **Angular** (current major: follow repo `package.json`), **TypeScript strict**, no third-party **UI** frameworks (no Bootstrap, Angular Material, PrimeNG, Tailwind-as-utility-framework, etc.). Plain CSS (or SCSS if the project already uses it) only.

## Angular architecture

- **Standalone components** only (do not add NgModules). Do not set `standalone: true` in decorators when the project’s Angular version treats standalone as default.
- **One component per concern**; **per-component stylesheet** (`styleUrl` / `styleUrls` relative to the component file).
- **State:** `signal` / `computed` / `effect` for local and shared feature state where appropriate; prefer `input()` / `output()` over `@Input` / `@Output`.
- **Change detection:** `ChangeDetectionStrategy.OnPush` on components.
- **Templates:** native control flow (`@if`, `@for`, `@switch`); **no** `*ngIf` / `*ngFor`. **No** `ngClass` / `ngStyle` — use `class` and `style` bindings.
- **Forms:** Reactive forms (`FormBuilder`, `FormGroup`) for non-trivial forms.
- **DI:** `inject()` instead of constructor injection for new code.
- **Images:** `NgOptimizedImage` for **static** image assets (not inline base64).
- **Routing:** lazy-loaded feature routes for sizeable areas.
- **Accessibility:** meet **WCAG AA** where applicable; sensible headings, labels, focus, and ARIA when native semantics are insufficient.

## CSS and layout

- **No UI kit dependency** — design with custom, token-friendly CSS (CSS variables for color, spacing, typography if helpful).
- **Layout:** prefer **Flexbox** and **CSS Grid** for structure, alignment, and responsive behavior.
- **Avoid** deep global overrides; keep styles **scoped to the component** (Angular emulates encapsulation by default).
- **No** inline layout hacks with tables or absolute positioning unless there is no reasonable flex/grid alternative.

## Node + Angular project shape

- Keep a clear **boundary**: Node serves API (and may serve the built Angular app in production); Angular lives in its **own app folder** (e.g. `client/`, `frontend/`, or workspace project).
- **Environment-specific** API URLs and flags: use Angular `environment` files or build-time replacement; never commit secrets.
- **CORS and security:** configure the Node app appropriately for the Angular dev server origin in development.

## File and naming conventions

- **PascalCase** component class names; **kebab-case** file names (`feature-list.component.ts`).
- **Co-locate:** `feature.component.ts`, `feature.component.html`, `feature.component.css` (or `.scss`).
- **Services:** `providedIn: 'root'` unless a narrower scope is required.

## When adding features

1. Identify or create a **feature folder** and **lazy route** if the feature is non-trivial.
2. Add **presentational** vs **container** split when it improves clarity (smart/dumb components).
3. Implement **API types** (interfaces/types) shared or mirrored between Node DTOs and Angular models as the project dictates.
4. Style with **flex/grid** first; verify keyboard navigation and focus for interactive controls.

## Anti-patterns (decline unless user explicitly overrides)

- Installing CSS frameworks, component libraries, or icon packs that replace core layout/styling responsibility.
- Global `!important` stacks to fight encapsulation.
- `any` in TypeScript; use `unknown` and narrow.
- Arrow functions in Angular templates (not supported).
