// Point d'entrée unique de la PWA (chargé en <script type="module">).
// Orchestre le boot : hauteur d'app, greeting, config (API key), premier
// rendu du dashboard. Le reste du code vit encore dans legacy.js, découpé
// progressivement en modules dédiés (overlays, chat, composer — step 05).
import { setApiKey } from "./state.js";
import { showToast } from "./ui.js";
import { fetchConfig } from "./api.js";
import { loadDashboard } from "./dashboard.js";
import { renderGreeting } from "./legacy.js";

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  setupAppHeight();
  renderGreeting();
  try {
    const cfg = await fetchConfig();
    setApiKey(cfg.api_key);
  } catch (e) {
    // /config est sur le même réseau Tailscale ; un échec ici est critique.
    showToast("Configuration indisponible");
  }
  await loadDashboard();
  // Refresh périodique léger pour le count notif et la météo (toutes les 2 min).
  setInterval(loadDashboard, 120_000);
});

// Pilote --app-h depuis window.visualViewport.height (fallback innerHeight).
// Sur iOS 26 PWA standalone, 100dvh peut renvoyer une valeur qui ne reflète
// pas la viewport visuelle réelle, ce qui laissait apparaître une bande
// vide sous la composer bar. visualViewport remonte la vraie hauteur
// utilisable (et s'ajuste quand le clavier s'ouvre/se ferme).
function setupAppHeight() {
  // Mesure unique au boot : la viewport disponible (= sans clavier).
  // On ne ré-écoute PAS les resize / visualViewport.resize : sur iOS,
  // ces events se déclenchent aussi à l'ouverture du clavier, ce qui
  // ferait chuter --app-h et écraserait le #app en haut de l'écran.
  // En gardant la valeur initiale, iOS translate automatiquement le
  // visual viewport pour amener l'input au-dessus du clavier.
  const measure = () => {
    const h = window.visualViewport?.height || window.innerHeight;
    document.documentElement.style.setProperty("--app-h", `${h}px`);
  };
  measure();
  // Seul l'orientationchange justifie une nouvelle mesure : c'est un
  // vrai changement de viewport, indépendant du clavier.
  window.addEventListener("orientationchange", () => {
    // Petit délai pour laisser iOS finir sa rotation avant de mesurer.
    setTimeout(measure, 150);
  });
}
