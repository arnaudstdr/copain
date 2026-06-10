// Point d'entrée unique de la PWA (chargé en <script type="module">).
// Orchestre le boot (hauteur d'app, greeting, config, premier rendu du
// dashboard) et centralise le câblage des listeners du DOM statique —
// les listeners du DOM dynamique (cards, rows) restent dans les renderers.
import { setApiKey, PROFILE_NAME } from "./state.js";
import { showToast, hideEphemeral } from "./ui.js";
import { fetchConfig } from "./api.js";
import { loadDashboard, closeBudget } from "./dashboard.js";
import { closeMarkdownView } from "./markdown.js";
import { openNotifs, closeNotifs, closeTasks, closeWeather, closeEvents, closeForYou } from "./overlays.js";
import {
  send, handleKey, updateSendBtn, updateChatSendBtn, autoResize,
  handleFileChange, removeAttachment, toggleMic, toggleChatMic,
} from "./composer.js";
import {
  openChat, closeChat, chatSend, handleChatKey,
  handleChatFileChange, removeChatAttachment,
} from "./chat.js";

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
  // Après la 1re révélation échelonnée des cards, on fige : les refresh
  // périodiques ne doivent pas rejouer l'animation (cf. animations.css).
  document.getElementById("dashboard").classList.add("revealed");
  // Refresh périodique léger pour le count notif et la météo (toutes les 2 min).
  setInterval(loadDashboard, 120_000);
});

function renderGreeting() {
  document.getElementById("greeting-name").textContent = `Bonjour ${PROFILE_NAME}`;
  const d = new Date();
  const opts = { weekday: "long", day: "numeric", month: "long" };
  document.getElementById("greeting-date").textContent = d.toLocaleDateString("fr-FR", opts);
}

// Pilote la hauteur du #app en distinguant deux états (le bug venait de les
// traiter pareil avec visualViewport.height) :
//
//  • Clavier FERMÉ → --app-h = 100% du web view (layout viewport). En PWA
//    standalone iOS, visualViewport.height au repos exclut une bande en bas,
//    ce qui laissait le #app trop court → bande vide sous la composer. 100%
//    remplit tout l'écran ; la safe-area-inset-bottom de la composer gère le
//    home indicator.
//  • Clavier OUVERT → --app-h = visualViewport.height + translate de
//    offsetTop. Le #app rétrécit pile à la zone visible au-dessus du clavier,
//    donc la composer reste visible. Figer la hauteur (ancien comportement)
//    laissait le #app plus grand que la zone visible → la barre passait
//    derrière le clavier et iOS sur-scrollait (il fallait swiper pour la voir).
//
// Détection clavier : un écart > 150px entre innerHeight (stable en standalone)
// et la hauteur visuelle = clavier ouvert.
function setupAppHeight() {
  const vv = window.visualViewport;
  const root = document.documentElement;
  const app = document.getElementById("app");
  const update = () => {
    const keyboardOpen = vv && window.innerHeight - vv.height > 150;
    if (vv && keyboardOpen) {
      root.style.setProperty("--app-h", `${vv.height}px`);
      app.style.transform = `translateY(${vv.offsetTop}px)`;
    } else {
      root.style.setProperty("--app-h", "100%");
      app.style.transform = "";
    }
  };
  update();
  vv?.addEventListener("resize", update);
  vv?.addEventListener("scroll", update);
  // Petit délai pour laisser iOS finir sa rotation avant de mesurer.
  window.addEventListener("orientationchange", () => setTimeout(update, 150));
}

// ── Bindings DOM ──────────────────────────────────────────────────────────
// Remplacent les attributs on* inline du HTML statique : en
// <script type="module">, les fonctions ne sont plus dans le scope global,
// les handlers inline (onclick="send()" …) ne les voient donc plus.
// Le module est différé par nature : le DOM statique est déjà parsé ici.
function bindStaticHandlers() {
  const $ = (sel) => document.querySelector(sel);
  // Ferme l'overlay uniquement au tap sur le fond (équivalent de l'ancien
  // onclick="if(event.target===this)closeX()").
  const closeOnBackdrop = (close) => (e) => { if (e.target === e.currentTarget) close(); };

  // Header
  $("#chat-btn").addEventListener("click", openChat);
  $("#bell-btn").addEventListener("click", openNotifs);

  // Bulle éphémère
  $("#ephemeral").addEventListener("click", hideEphemeral);

  // Composer du dashboard
  $("#preview-bar .remove-btn").addEventListener("click", removeAttachment);
  $("#file-input").addEventListener("change", handleFileChange);
  $('#bar .icon-btn[title="Joindre une photo"]').addEventListener("click", () => $("#file-input").click());
  $("#mic-btn").addEventListener("click", toggleMic);
  const msgInput = $("#msg-input");
  msgInput.addEventListener("keydown", handleKey);
  msgInput.addEventListener("input", () => { autoResize(msgInput); updateSendBtn(); });
  $("#send-btn").addEventListener("click", send);

  // Overlays (tap sur le fond + bouton croix)
  $("#notif-overlay").addEventListener("click", closeOnBackdrop(closeNotifs));
  $("#notif-overlay .close-btn").addEventListener("click", closeNotifs);
  $("#tasks-overlay").addEventListener("click", closeOnBackdrop(closeTasks));
  $("#tasks-overlay .close-btn").addEventListener("click", closeTasks);
  $("#weather-overlay").addEventListener("click", closeOnBackdrop(closeWeather));
  $("#weather-overlay .close-btn").addEventListener("click", closeWeather);
  $("#events-overlay").addEventListener("click", closeOnBackdrop(closeEvents));
  $("#events-overlay .close-btn").addEventListener("click", closeEvents);
  $("#foryou-overlay").addEventListener("click", closeOnBackdrop(closeForYou));
  $("#foryou-overlay .close-btn").addEventListener("click", closeForYou);
  $("#budget-overlay").addEventListener("click", closeOnBackdrop(closeBudget));
  $("#budget-overlay .close-btn").addEventListener("click", closeBudget);

  // Vue markdown
  $("#markdown-view header .header-btn").addEventListener("click", closeMarkdownView);

  // Mode chat
  $("#chat-view header .header-btn").addEventListener("click", closeChat);
  $("#chat-preview-bar .remove-btn").addEventListener("click", removeChatAttachment);
  $("#chat-file-input").addEventListener("change", handleChatFileChange);
  $('#chat-bar .icon-btn[title="Joindre une photo"]').addEventListener("click", () => $("#chat-file-input").click());
  $("#chat-mic-btn").addEventListener("click", toggleChatMic);
  const chatInput = $("#chat-input");
  chatInput.addEventListener("keydown", handleChatKey);
  chatInput.addEventListener("input", () => { autoResize(chatInput); updateChatSendBtn(); });
  $("#chat-send-btn").addEventListener("click", chatSend);
}
bindStaticHandlers();
