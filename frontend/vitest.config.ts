import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
    // e2e/ belongs to Playwright (npm run test:contrast); it needs a real
    // browser to resolve the CSS cascade, which jsdom cannot do.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
