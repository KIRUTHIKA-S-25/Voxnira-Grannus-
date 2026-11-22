# Grannus Web App

The Grannus frontend is a Next.js patient and care-team interface for multilingual voice intake, consultation review, and follow-up. It communicates with the FastAPI backend and Supabase.

## Requirements

- Node.js 20 or newer
- npm
- A running Grannus backend
- Supabase project URL and anon key

## Local development

From this directory, install dependencies:

```bash
npm install
```

Copy `.env.example` to `.env.local` and configure the values:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key
NEXT_PUBLIC_GRIEVANCE_OFFICER_EMAIL=your-designated-contact@example.org
```

The Supabase anon key is intended for browser use; never put a service-role key or other secret in a `NEXT_PUBLIC_*` variable. The public grievance contact should match the backend's configured contact.

Start the development server:

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The backend should be available at the URL configured in `NEXT_PUBLIC_API_URL`.

## Available scripts

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start the local development server |
| `npm run build` | Create a production build |
| `npm run start` | Serve the production build |
| `npm run lint` | Run ESLint |

## Production build

Set the `NEXT_PUBLIC_*` values before building. Next.js embeds these public values into the browser bundle at build time, so changing container runtime variables alone will not update them. The root Docker Compose configuration supplies build arguments for the frontend.

```bash
npm run build
npm run start
```

For container deployment, see the root [`docker-compose.yml`](../docker-compose.yml) and [deployment runbook](../docs/DEPLOYMENT_RUNBOOK.md). For project architecture and safety scope, see the [repository README](../README.md).
