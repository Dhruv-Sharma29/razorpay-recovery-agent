/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Deployed API origin, no trailing slash. */
  readonly VITE_API_BASE?: string;
  /**
   * Value for the X-API-Key header. Baked into the bundle at build time and
   * therefore public — a traffic deterrent, not a credential.
   */
  readonly VITE_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
