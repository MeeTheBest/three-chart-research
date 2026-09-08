import vinext from "vinext";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vinext()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8788" },
    watch: process.env.CODEX_SANDBOX === "seatbelt" ? { useFsEvents: false, usePolling: true } : undefined,
  },
});
