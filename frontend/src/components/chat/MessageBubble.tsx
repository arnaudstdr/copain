// ── Bulle du fil de discussion ──────────────────────────────────────────────
// Utilisateur : texte brut (pré-wrap). Assistant : markdown (`chat-md`, même
// rendu que l'actu — cf. Markdown.tsx). Erreur : pictogramme + message FR.
// Photo jointe (imgSrc, session uniquement) rendue en tête de bulle.
// Portage de `makeChatRow` (bot/static/js/chat.js).

import { AlertTriangle, Bot } from "lucide-react";
import { Markdown } from "../Markdown";
import type { ChatMessage } from "../../lib/chatStore";

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`row ${isUser ? "user" : "bot"}`}>
      {!isUser && (
        <div className="avatar-sm">
          <Bot size={16} />
        </div>
      )}
      <div className={`bubble ${isUser ? "user" : "bot"}${message.error ? " error" : ""}`}>
        {message.imgSrc && <img className="bubble-img" src={message.imgSrc} alt="photo" />}
        {message.error ? (
          <span className="bubble-text">
            <AlertTriangle size={14} className="lucide-warn" /> {message.text}
          </span>
        ) : isUser ? (
          message.text ? <span className="bubble-text">{message.text}</span> : null
        ) : (
          <Markdown className="bubble-text chat-md">{message.text}</Markdown>
        )}
      </div>
    </div>
  );
}
