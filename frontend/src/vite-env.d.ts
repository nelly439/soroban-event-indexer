/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the indexer API. Defaults to "/api" (proxied by Vite / nginx). */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
