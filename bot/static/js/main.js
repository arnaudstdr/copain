// Point d'entrée unique de la PWA (chargé en <script type="module">).
// Orchestre le boot (hauteur d'app, greeting, config, premier rendu du
// dashboard) et centralise le câblage des listeners du DOM statique —
// les listeners du DOM dynamique (cards, rows) restent dans les renderers.
import { setApiKey, PROFILE_NAME } from "./state.js?v=13";
import { showToast, hideEphemeral } from "./ui.js?v=14";
import { fetchConfig } from "./api.js?v=13";
import { loadDashboard, closeBudget } from "./dashboard.js?v=16";
import { closeMarkdownView } from "./markdown.js?v=13";
import {
  openNotifs, closeNotifs, closeTasks, closeWeather, closeEvents, closeForYou,
  closeDepot, submitDepot, toggleDepotChip,
} from "./overlays.js?v=16";
import {
  send, handleKey, updateSendBtn, updateChatSendBtn, autoResize,
  handleFileChange, removeAttachment, toggleMic, toggleChatMic,
} from "./composer.js?v=14";
import {
  openChat, closeChat, chatSend, handleChatKey,
  handleChatFileChange, removeChatAttachment,
} from "./chat.js?v=14";

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
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

// NB : plus de gestion JS de la hauteur (--app-h / visualViewport). Le
// document est désormais le scroller racine (cf. layout.css) : WebKit 26
// gère nativement le clavier au-dessus des éléments fixed dans ce modèle.

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
  $("#depot-overlay").addEventListener("click", closeOnBackdrop(closeDepot));
  $("#depot-overlay .close-btn").addEventListener("click", closeDepot);
  $("#depot-submit").addEventListener("click", submitDepot);
  document.querySelectorAll("#depot-overlay .depot-chip")
    .forEach((chip) => chip.addEventListener("click", () => toggleDepotChip(chip)));
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
