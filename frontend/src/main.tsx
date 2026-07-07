import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import { ToastProvider } from "./components/Toast.tsx";
import { bootstrapConfig } from "./api/client.ts";
import "./index.css";

// Précharge la clé API (GET /config) dès le démarrage pour que les premiers
// appels authentifiés ne paient pas l'aller-retour. Best-effort : en cas
// d'échec, `ensureApiKey` réessaiera paresseusement au premier appel.
void bootstrapConfig();

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Élément racine #root introuvable");
}

createRoot(rootEl).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
);

// Service worker (network-first sur l'API, cf. public/sw.js). Enregistré
// uniquement en contexte de production servi par FastAPI ; en `vite dev`
// on évite d'interférer avec le HMR.
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Échec silencieux : l'app fonctionne sans le service worker.
    });
  });
}
