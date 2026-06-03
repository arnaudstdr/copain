// ── Mode chat : overlay plein écran, feed, pièce jointe, envoi SSE ────────
import {
  loading,
  chatAttachment, setChatAttachment,
  chatHistory,
} from "./state.js";
import { el, lucideNode } from "./ui.js";
import { callImage, callTextStream } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { loadDashboard, invalidateCards } from "./dashboard.js";
import { setLoading, autoResize, updateChatSendBtn } from "./composer.js";

// ── Mode chat (overlay) ───────────────────────────────────────────────────
export function openChat() {
  document.getElementById("chat-view").classList.remove("hidden");
  renderChatFeed();
  setTimeout(() => document.getElementById("chat-input").focus(), 50);
}
export function closeChat() {
  document.getElementById("chat-view").classList.add("hidden");
}
function renderChatFeed() {
  const feed = document.getElementById("chat-feed");
  feed.innerHTML = "";
  if (chatHistory.length === 0) {
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
  chatHistory.forEach(m => feed.appendChild(makeChatRow(m.role, m.text, m.imgSrc, m.error)));
  feed.scrollTop = feed.scrollHeight;
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
  chatHistory.push({ role: "user", text, imgSrc: att?.preview ?? null });
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
      chatHistory.push({ role: "assistant", text: body.response });
      if (body.refresh_cards && body.refresh_cards.length > 0) {
        invalidateCards(body.refresh_cards);
        loadDashboard();
      }
    } catch (e) {
      chatHistory.push({ role: "assistant", text: "Impossible de joindre Copain.", error: true });
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
      chatHistory.push({ role: "assistant", text: streamError, error: true });
    } else {
      chatHistory.push({ role: "assistant", text: acc });
    }
  } catch (e) {
    chatHistory.push({ role: "assistant", text: "Impossible de joindre Copain.", error: true });
  } finally {
    setLoading(false, "chat");
    document.getElementById("chat-typing")?.remove();
    document.getElementById("chat-live")?.remove();
    renderChatFeed();
  }
}

export function handleChatKey(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chatSend(); } }
