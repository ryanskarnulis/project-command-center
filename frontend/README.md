# Project Command Center Frontend

React + Vite + TypeScript client for Project Command Center.

For backend setup, AI workflows, Discord setup, database notes, and full-project
development commands, use the root [README](../README.md).

## Setup

```sh
npm install
```

## Commands

```sh
npm run dev        # start the Vite dev server
npm run build      # type-check and build production assets
npm run lint       # run ESLint
npm run test       # run Vitest once
npm run test:watch # run Vitest in watch mode
npm run preview    # preview the production build locally
```

## Environment

Copy `frontend/.env.example` to `frontend/.env` when local overrides are needed.

- `DEV_HOST`: dev server bind address. Defaults to `127.0.0.1`; set to
  `0.0.0.0` to expose the frontend on the LAN.
- `VITE_API_URL`: explicit FastAPI backend URL. When unset, the client derives
  `http://<window.location.hostname>:8000`, which works for local and LAN access
  when the backend is bound appropriately.

The frontend expects the FastAPI backend to be reachable at
`http://<host>:8000` unless `VITE_API_URL` is set.
