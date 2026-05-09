# GINHAWA Portal

Web portal for Barangay Health Workers (BHWs) to review kiosk-captured
health measurements. Companion to the [GINHAWA kiosk](../kiosk) and
[cloud backend](../cloud).

## Features

- Username/password login (JWT auth)
- Dashboard with KPIs, sessions-per-day chart, path breakdown, recent
  activity
- Sessions list with filters (status, path, date, citizen, barangay)
- Session detail with measurements, citizen lookup, and audit timeline
- Citizens directory with detail pages
- Audit log (admin-only) with action and date filters
- Measurement invalidate flow (admin-only) with reason capture

## Tech stack

- React 19 + TypeScript (strict mode)
- Vite for build/dev
- React Router v7
- TanStack Query v5 for server state
- Recharts for the dashboard charts
- MSW for component-level testing
- Plain CSS modules + a single token file (`src/index.css`); no Tailwind,
  no UI library

## Setup

### Prerequisites

- Node.js 20+
- GINHAWA cloud reachable at `http://127.0.0.1:8000` (or set
  `VITE_CLOUD_API_URL`)

### Install

```
npm install
```

### Run dev server

```
npm run dev
```

Opens on http://localhost:5173.

### Build

```
npm run build
```

Output goes to `dist/`.

### Test and lint

```
npm run test
npm run lint
```

## Demo credentials (dev only)

Created by the cloud's `seed_dev_data.py` script. **Do not use in
production.**

| Username           | Password                        | Role  |
| ------------------ | ------------------------------- | ----- |
| `admin`            | `seed_admin_password_change_me` | admin |
| `bhw_tibagan`      | `seed_bhw_password`             | bhw   |
| `bhw_pinaglabanan` | `seed_bhw_password`             | bhw   |
| `bhw_corazon`      | `seed_bhw_password`             | bhw   |

## Configuration

Environment variables (`.env.development`):

- `VITE_CLOUD_API_URL` — cloud API base URL (default
  `http://127.0.0.1:8000`)

## Architecture

```
src/
  api/         API client, error types, token storage
  auth/        AuthContext, useAuth hook, AuthProvider
  components/  Reusable UI (ConfirmModal, Toast, Skeleton, EmptyState,
               StatusPill, ErrorBoundary, Pagination)
  hooks/       Query hooks (useDashboardStats, useCitizens, …)
  layouts/     AppLayout (header, sidebar, footer)
  pages/       Route-level pages (Login, Dashboard, Sessions,
               SessionDetail, Citizens, CitizenDetail, AuditLog)
  utils/       Pure utilities (date math, dashboard aggregations)
  __tests__/   Vitest + MSW integration tests
  index.css    Color tokens (single source of truth) + base styles
```

Notable conventions:

- Hand-written API types mirror the cloud's pydantic schemas; we
  intentionally do not use openapi-typescript codegen so the contract
  reads as documentation.
- Server state is owned by TanStack Query. Local state stays in
  `useState`/`useReducer`. There is no Redux.
- Color tokens live in `src/index.css` under `:root`. Re-skinning is a
  token swap, not a global find-and-replace.
- Tables that opt into the `responsive-table` class collapse to
  card-style rows under 768px; each `<td>` carries a `data-label`
  attribute that becomes the row label on mobile.

## Deployment

Build with `npm run build`, then serve `dist/` from any static host
(nginx, Apache, S3+CloudFront, Caddy). Configure the reverse proxy to
forward `/api/v1/*` to the cloud API and to fall back to `/index.html`
for client-side routes (SPA fallback).

A minimal nginx snippet:

```nginx
server {
  root /var/www/ginhawa-portal;
  index index.html;

  location /api/ {
    proxy_pass http://cloud.internal:8000;
  }

  location / {
    try_files $uri /index.html;
  }
}
```

## Demo notes

- The portal's `localhost` dev server expects the cloud at
  `127.0.0.1:8000`. Run the cloud first, seed the DB, then start the
  portal — login fails fast otherwise (no offline fallback).
- The dashboard's data window is 14 days; if you've just seeded the DB,
  use the seed script's `--days-back` flag to populate enough sessions
  for the chart to look populated.
- The measurement invalidate flow is admin-only. Sign in as `admin` to
  see the "Invalidate" button on a session-detail row.
