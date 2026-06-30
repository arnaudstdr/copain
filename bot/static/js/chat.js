// ── Mode chat : overlay plein écran, feed, pièce jointe, envoi SSE ────────
import {
  loading,
  chatAttachment, setChatAttachment,
  chatHistory, chatHistoryState,
} from "./state.js?v=13";
import { el, lucideNode, sameDay, formatDaySeparator } from "./ui.js?v=14";
import { callImage, callTextStream, fetchHistory } from "./api.js?v=13";
import { renderMarkdown } from "./markdown.js?v=13";
import { loadDashboard, invalidateCards } from "./dashboard.js?v=15";
import { setLoading, autoResize, updateChatSendBtn } from "./composer.js?v=13";

// ── Mode chat (overlay) ───────────────────────────────────────────────────
let scrollBound = false;
export function openChat() {
  document.getElementById("chat-view").classList.remove("hidden");
  bindFeedScroll();
  renderChatFeed();
  if (!chatHistoryState.loaded) hydrateHistory();
  setTimeout(() => document.getElementById("chat-input").focus(), 50);
}
export function closeChat() {
  document.getElementById("chat-view").classList.add("hidden");
}

// Hydrate le fil depuis la persistance serveur à la 1re ouverture de session.
// Les bulles déjà envoyées dans cette session (présentes en mémoire) sont
// conservées et placées après l'historique rechargé.
async function hydrateHistory() {
  try {
    const data = await fetchHistory(50);
    const restored = (data.messages || []).map(m => ({
      id: m.id, role: m.role, text: m.content, createdAt: m.created_at,
    }));
    chatHistory.unshift(...restored);
    chatHistoryState.hasMore = !!data.has_more;
    chatHistoryState.oldestId = restored.length ? restored[0].id : null;
  } catch {
    // Réaffichage non critique : en cas d'échec on garde un fil vide.
  } finally {
    chatHistoryState.loaded = true;
    renderChatFeed();
  }
}

// Charge une page plus ancienne quand on scrolle en haut du fil, en
// préservant la position de lecture (pas de saut visuel après le prepend).
async function loadOlder() {
  if (chatHistoryState.loadingOlder || !chatHistoryState.hasMore || chatHistoryState.oldestId == null) return;
  chatHistoryState.loadingOlder = true;
  const feed = document.getElementById("chat-feed");
  const prevHeight = feed.scrollHeight;
  try {
    const data = await fetchHistory(50, chatHistoryState.oldestId);
    const older = (data.messages || []).map(m => ({
      id: m.id, role: m.role, text: m.content, createdAt: m.created_at,
    }));
    if (older.length) {
      chatHistory.unshift(...older);
      chatHistoryState.oldestId = older[0].id;
    }
    chatHistoryState.hasMore = !!data.has_more;
    renderChatFeed({ scrollToBottom: false });
    feed.scrollTop = feed.scrollHeight - prevHeight;  // conserve la vue
  } catch {
    // silencieux : on réessaiera au prochain scroll vers le haut.
  } finally {
    chatHistoryState.loadingOlder = false;
  }
}

function bindFeedScroll() {
  if (scrollBound) return;
  const feed = document.getElementById("chat-feed");
  feed.addEventListener("scroll", () => {
    if (feed.scrollTop < 40) loadOlder();
  });
  scrollBound = true;
}

function renderChatFeed({ scrollToBottom = true } = {}) {
  const feed = document.getElementById("chat-feed");
  feed.innerHTML = "";
  if (chatHistory.length === 0) {
    // Avant la fin de l'hydratation, on laisse le fil vide pour éviter un
    // flash du message d'accueil suivi de l'historique rechargé.
    if (!chatHistoryState.loaded) return;
    const row = el("div", "row bot");
    const avatar = el("div", "avatar-sm");
    avatar.appendChild(lucideNode("bot", 16));
    row.appendChild(avatar);
    const bubble = el("div", "bubble bot");
    bubble.appendChild(el("span", "bubble-text", "On peut discuter ici sans que ça pollue ton dashboard. Vas-y."));
    row.appendChild(bubble);
    feed.appendChild(row);
    return;
  }
  let lastDay = null;
  chatHistory.forEach(m => {
    const d = m.createdAt ? new Date(m.createdAt) : new Date();
    if (!isNaN(d.getTime()) && (!lastDay || !sameDay(d, lastDay))) {
      feed.appendChild(makeDaySeparator(formatDaySeparator(d)));
      lastDay = d;
    }
    feed.appendChild(makeChatRow(m.role, m.text, m.imgSrc, m.error));
  });
  if (scrollToBottom) feed.scrollTop = feed.scrollHeight;
}

function makeDaySeparator(label) {
  const sep = el("div", "chat-day-sep");
  sep.appendChild(el("span", null, label));
  return sep;
}
function makeChatRow(role, text, imgSrc, error) {
  const row = el("div", `row ${role === "user" ? "user" : "bot"}`);
  if (role !== "user") {
    const avatar = el("div", "avatar-sm");
    avatar.appendChild(lucideNode("bot", 16));
    row.appendChild(avatar);
  }
  const bubble = el("div", `bubble ${role === "user" ? "user" : "bot"}${error ? " error" : ""}`);
  if (imgSrc) {
    const img = document.createElement("img");
    img.src = imgSrc;
    img.className = "bubble-img";
    img.alt = "photo";
    bubble.appendChild(img);
  }
  if (text) {
    const span = el("span", "bubble-text");
    if (error) {
      span.appendChild(lucideNode("alert-triangle", 14, "lucide-warn"));
      span.appendChild(document.createTextNode(" " + text));
    } else if (role === "user") {
      span.appendChild(document.createTextNode(text));
    } else {
      // Réponse du bot : markdown rendu (échappé HTML dans renderMarkdown).
      span.classList.add("chat-md");
      span.innerHTML = renderMarkdown(text);
    }
    bubble.appendChild(span);
  }
  row.appendChild(bubble);
  return row;
}

export function handleChatFileChange(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const result = reader.result;
    setChatAttachment({ b64: result.split(",")[1], mediaType: file.type, preview: result });
    document.getElementById("chat-preview-img").src = result;
    document.getElementById("chat-preview-bar").classList.remove("hidden");
    document.getElementById("chat-input").placeholder = "Ajoute un mot…";
    updateChatSendBtn();
  };
  reader.readAsDataURL(file);
  e.target.value = "";
}

export function removeChatAttachment() {
  setChatAttachment(null);
  document.getElementById("chat-preview-bar").classList.add("hidden");
  document.getElementById("chat-preview-img").src = "";
  document.getElementById("chat-input").placeholder = "Écris…";
  updateChatSendBtn();
}

export async function chatSend() {
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if ((!text && !chatAttachment) || loading) return;
  setLoading(true, "chat");

  // Snapshot la pièce jointe avant de reset, pour pouvoir l'afficher
  // dans la bulle utilisateur et la transmettre à callImage.
  const att = chatAttachment;
  chatHistory.push({ role: "user", text, imgSrc: att?.preview ?? null, createdAt: new Date().toISOString() });
  removeChatAttachment();
  renderChatFeed();
  input.value = ""; autoResize(input); updateChatSendBtn();

  // Indicateur "en train d'écrire"
  const feed = document.getElementById("chat-feed");
  const typing = el("div", "row bot typing-row");
  typing.id = "chat-typing";
  const typingAvatar = el("div", "avatar-sm");
  typingAvatar.appendChild(lucideNode("bot", 16));
  typing.appendChild(typingAvatar);
  const tb = el("div", "bubble bot typing");
  tb.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
  typing.appendChild(tb); feed.appendChild(typing); feed.scrollTop = feed.scrollHeight;

  // Photos : pas de streaming (multimodal via /ask/image, réponse en un bloc).
  if (att) {
    try {
      const body = await callImage(text, att);
      chatHistory.push({ role: "assistant", text: body.response, createdAt: new Date().toISOString() });
      if (body.refresh_cards && body.refresh_cards.length > 0) {
        invalidateCards(body.refresh_cards);
        loadDashboard();
      }
    } catch (e) {
      chatHistory.push({ role: "assistant", text: "Impossible de joindre Copain.", error: true, createdAt: new Date().toISOString() });
    } finally {
      setLoading(false, "chat");
      document.getElementById("chat-typing")?.remove();
      renderChatFeed();
    }
    return;
  }

  // Texte : streaming SSE. Une bulle "live" remplace l'indicateur typing au
  // premier delta et est re-rendue en markdown à chaque chunk ; à la fin,
  // renderChatFeed() reconstruit le feed proprement depuis chatHistory.
  let acc = "";
  let liveSpan = null;
  const renderLive = () => {
    if (!liveSpan) {
      document.getElementById("chat-typing")?.remove();
      const row = el("div", "row bot");
      row.id = "chat-live";
      const avatar = el("div", "avatar-sm");
      avatar.appendChild(lucideNode("bot", 16));
      row.appendChild(avatar);
      const bubble = el("div", "bubble bot");
      liveSpan = el("span", "bubble-text chat-md");
      bubble.appendChild(liveSpan);
      row.appendChild(bubble);
      feed.appendChild(row);
    }
    liveSpan.innerHTML = renderMarkdown(acc);
    feed.scrollTop = feed.scrollHeight;
  };

  let streamError = null;
  try {
    await callTextStream(text, {
      onDelta(t) { acc += t; renderLive(); },
      onReplace(t) { acc = t; renderLive(); },
      onDone(intent, refreshCards) {
        if (refreshCards && refreshCards.length > 0) {
          invalidateCards(refreshCards);
          loadDashboard();
        }
      },
      onError(t) { streamError = t || "Impossible de joindre Copain."; }
    });
    if (streamError) {
      chatHistory.push({ role: "assistant", text: streamError, error: true, createdAt: new Date().toISOString() });
    } else {
      chatHistory.push({ role: "assistant", text: acc, createdAt: new Date().toISOString() });
    }
  } catch (e) {
    chatHistory.push({ role: "assistant", text: "Impossible de joindre Copain.", error: true, createdAt: new Date().toISOString() });
  } finally {
    setLoading(false, "chat");
    document.getElementById("chat-typing")?.remove();
    document.getElementById("chat-live")?.remove();
    renderChatFeed();
  }
}

export function handleChatKey(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chatSend(); } }
