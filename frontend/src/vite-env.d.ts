/// <reference types="vite/client" />

interface ImportMetaEnv {
  // Optional: when unset, the client derives the API base from the current
  // host (window.location.hostname) so LAN devices reach the right backend.
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
