import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the UI runs on :5173 and forwards /api to the FastAPI server on :8080,
// so the browser sees one origin and the SameSite=Strict session cookie just works.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://localhost:8080", changeOrigin: false } },
  },
});
