// ── Composer : envoi /ask, photo, micro, état des boutons d'envoi ─────────
// Possède aussi les helpers partagés avec le mode chat (setLoading,
// autoResize, updateChatSendBtn) : chat.js les importe d'ici, jamais
// l'inverse — pas de cycle composer ↔ chat.
import {
  loading, setLoadingFlag,
  attachment, setAttachment,
  chatAttachment,
} from "./state.js";
import { el, lucideNode, showToast, showEphemeral } from "./ui.js";
import { callText, callImage } from "./api.js";
import { loadDashboard, flashCards } from "./dashboard.js";

// ── État local du module ──────────────────────────────────────────────────
// Instance SpeechRecognition partagée entre les deux micros (dashboard et
// chat) : un seul enregistrement à la fois, les deux toggles vivent ici.
let recognition  = null;

// ── Envoi /ask ────────────────────────────────────────────────────────────
export async function send() {
  const text = document.getElementById("msg-input").value.trim();
  if ((!text && !attachment) || loading) return;

  setLoading(true);
  const att = attachment;
  removeAttachment();
  document.getElementById("msg-input").value = "";
  autoResize(document.getElementById("msg-input"));
  updateSendBtn();

  try {
    const body = att
      ? await callImage(text, att)
      : await callText(text);
    handleAskResponse(body, text);
  } catch (e) {
    showEphemeral("Impossible de joindre Copain. Vérifie Tailscale.", true);
  } finally {
    setLoading(false);
  }
}

function handleAskResponse(body, userText) {
  const intent = body.intent || "answer";
  const refresh = body.refresh_cards || [];

  // Mode action : toast court + rafraîchissement des cards concernées
  if (refresh.length > 0) {
    showToast(actionToast(intent));
    loadDashboard().then(() => flashCards(refresh));
  } else {
    // Mode question : on affiche la réponse texte en éphémère
    showEphemeral(body.response, false);
  }
}

function actionToast(intent) {
  let label;
  switch (intent) {
    case "task":   label = "Tâche ajoutée"; break;
    case "event":  label = "Évènement créé"; break;
    case "feed":   label = "Flux mis à jour"; break;
    case "memory": label = "Noté en mémoire"; break;
    case "expense":label = "Saisie enregistrée"; break;
    default:       label = "Fait";
  }
  const wrap = el("span", "toast-content");
  wrap.appendChild(lucideNode("check", 14));
  wrap.appendChild(document.createTextNode(label));
  return wrap;
}

// ── Photo ─────────────────────────────────────────────────────────────────
export function handleFileChange(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const result = reader.result;
    setAttachment({ b64: result.split(",")[1], mediaType: file.type, preview: result });
    document.getElementById("preview-img").src = result;
    document.getElementById("preview-bar").classList.remove("hidden");
    document.getElementById("msg-input").placeholder = "Ajoute un mot…";
    updateSendBtn();
  };
  reader.readAsDataURL(file);
  e.target.value = "";
}

export function removeAttachment() {
  setAttachment(null);
  document.getElementById("preview-bar").classList.add("hidden");
  document.getElementById("preview-img").src = "";
  document.getElementById("msg-input").placeholder = "Écris un mot…";
  updateSendBtn();
}

// ── Mic (Web Speech API) ──────────────────────────────────────────────────
export function toggleMic() { _toggleMic("mic-btn", "msg-input", updateSendBtn); }
export function toggleChatMic() { _toggleMic("chat-mic-btn", "chat-input", updateChatSendBtn); }
function _toggleMic(btnId, inputId, updateBtn) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { showToast("Micro non supporté ici (HTTPS requis)"); return; }
  if (recognition) {
    recognition.stop(); recognition = null;
    document.getElementById(btnId).classList.remove("recording");
    return;
  }
  const r = new SR();
  r.lang = "fr-FR"; r.continuous = false; r.interimResults = false;
  recognition = r;
  document.getElementById(btnId).classList.add("recording");
  r.onresult = e => {
    const t = e.results[0][0].transcript;
    const input = document.getElementById(inputId);
    input.value = input.value ? `${input.value} ${t}` : t;
    autoResize(input); updateBtn();
  };
  r.onerror = () => { showToast("Erreur micro"); };
  r.onend = () => { recognition = null; document.getElementById(btnId).classList.remove("recording"); };
  r.start();
}

// ── État des boutons d'envoi (dashboard + chat) ───────────────────────────
export function setLoading(val, scope) {
  setLoadingFlag(val);
  if (scope === "chat") {
    document.getElementById("chat-send-btn").disabled = val || !canChatSend();
  } else {
    document.getElementById("send-btn").disabled = val || !canSend();
  }
}
function canSend() {
  return (document.getElementById("msg-input").value.trim() || !!attachment) && !loading;
}
function canChatSend() {
  return (document.getElementById("chat-input").value.trim() || !!chatAttachment) && !loading;
}
export function updateSendBtn() { document.getElementById("send-btn").disabled = !canSend(); }
export function updateChatSendBtn() { document.getElementById("chat-send-btn").disabled = !canChatSend(); }
export function handleKey(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }
export function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 110) + "px";
}
