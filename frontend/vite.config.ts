import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Préfixes des routes API de copain, tous servis à la racine par FastAPI.
// En dev (`vite dev`), Vite proxifie ces chemins vers le backend uvicorn
// (:8000) ; en prod c'est FastAPI qui sert le build (cf. step 02).
// Note : /ask couvre /ask/stream et /ask/image, /event couvre /event/location.
const API_PREFIXES = [
  "/ask",
  "/dashboard",
  "/thoughts",
  "/history",
  "/foryou",
  "/tasks",
  "/budget",
  "/expenses",
  "/weather",
  "/events",
  "/news",
  "/notifications",
  "/config",
  "/event",
  "/share",
];

const proxy = Object.fromEntries(
  API_PREFIXES.map((prefix) => [
    prefix,
    {
      target: "http://localhost:8000",
      changeOrigin: true,
      // SSE (/ask/stream) : http-proxy relaie le flux sans le bufferiser.
    },
  ]),
);

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
