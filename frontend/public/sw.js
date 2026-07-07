// Service worker copain — stratégie network-first.
//
// Objectif volontairement minimal (cf. SPEC, Décision 4) : ne jamais servir
// une version périmée de l'app. On tente toujours le réseau d'abord ; le
// cache n'est qu'un filet de secours hors-ligne pour les GET same-origin.
//
// Le nom du cache est VERSIONNÉ : bumper CACHE_NAME à chaque release invalide
// l'ancien cache au `activate`. Les assets du build Vite étant hashés, ils ne
// posent pas de problème de fraîcheur ; ce SW couvre surtout la navigation
// (index.html) et un fallback hors-ligne.

const CACHE_NAME = "copain-v1";

self.addEventListener("install", (event) => {
  // Prend la main immédiatement, sans attendre la fermeture des anciens
  // onglets (mono-utilisateur, pas de risque de version mixte).
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  // On ne gère que les GET same-origin ; le reste (POST /ask, SSE, API
  // cross-origin) passe directement au réseau, sans interception.
  if (request.method !== "GET" || new URL(request.url).origin !== self.location.origin) {
    return;
  }

  event.respondWith(
    (async () => {
      try {
        const response = await fetch(request);
        // Ne met en cache que les réponses complètes et réussies.
        if (response.ok && response.type === "basic") {
          const cache = await caches.open(CACHE_NAME);
          cache.put(request, response.clone());
        }
        return response;
      } catch (err) {
        // Réseau indisponible : on tente le cache.
        const cached = await caches.match(request);
        if (cached) {
          return cached;
        }
        // Navigation hors-ligne sans cache : on retombe sur l'index.
        if (request.mode === "navigate") {
          const fallback = await caches.match("/");
          if (fallback) {
            return fallback;
          }
        }
        throw err;
      }
    })(),
  );
});
