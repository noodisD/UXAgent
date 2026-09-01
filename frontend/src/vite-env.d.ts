/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Absolute origin of the backend API, e.g. "https://uxagent-backend.run.app".
   * Empty in development, where Vite proxies the API paths instead.
   */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
