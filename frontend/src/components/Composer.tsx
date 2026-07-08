// ── Composer d'entrée (dashboard + chat) ────────────────────────────────────
// Barre de saisie mutualisée : texte + pièce jointe photo (base64) + dictée
// vocale. Porte l'UI et l'état local (brouillon, photo, micro, auto-resize) ;
// l'envoi réel est délégué au parent via `onSend(text, attachment)` — le
// dashboard route vers /ask (bulle éphémère) et le chat vers /ask/stream ou
// /ask/image selon la présence d'une photo. Portage de bot/static/js/composer.js
// (send, handleFileChange, removeAttachment, _toggleMic, autoResize).

import { useLayoutEffect, useRef, useState } from "react";
import { Mic, Paperclip, Send, X } from "lucide-react";
import { useToast } from "./Toast";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";

const MAX_INPUT_HEIGHT = 110; // px — aligné sur .msg-input (max-height CSS)

/** Photo jointe : base64 sans préfixe `data:` + type MIME + aperçu (data URL). */
export interface Attachment {
  b64: string;
  mediaType: string;
  preview: string;
}

interface Props {
  /** Envoi effectif (le parent gère le réseau et l'état `busy`). */
  onSend: (text: string, attachment: Attachment | null) => void;
  /** Envoi en cours (désactive le bouton, comme le `loading` du vanilla). */
  busy: boolean;
  /** `chat` ajoute la classe de la barre du mode dialogue (z-index supérieur). */
  variant?: "dashboard" | "chat";
}

export function Composer({ onSend, busy, variant = "dashboard" }: Props) {
  const toast = useToast();
  const [draft, setDraft] = useState("");
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const { toggle: toggleMic, recording } = useSpeechRecognition({
    onResult: (t) => setDraft((d) => (d ? `${d} ${t}` : t)),
    onError: () => toast("Erreur micro"),
  });

  // Auto-resize de la zone de saisie (frappe ET insertion micro). À vide on
  // laisse `height: auto` (hauteur naturelle d'une ligne, robuste au chargement
  // différé de la webfont) et on ne fige une hauteur mesurée que s'il y a du
  // texte — sinon une mesure au montage (police pas encore prête) décale le
  // placeholder. Le vanilla n'appelait autoResize qu'après interaction.
  useLayoutEffect(() => {
    const ta = inputRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    if (draft) ta.style.height = `${Math.min(ta.scrollHeight, MAX_INPUT_HEIGHT)}px`;
  }, [draft]);

  const canSend = (draft.trim().length > 0 || attachment !== null) && !busy;

  const submit = () => {
    const text = draft.trim();
    if ((!text && !attachment) || busy) return;
    const att = attachment;
    setDraft("");
    setAttachment(null);
    onSend(text, att);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // autorise la re-sélection du même fichier
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      setAttachment({ b64: result.split(",")[1], mediaType: file.type, preview: result });
    };
    reader.readAsDataURL(file);
  };

  const handleMic = () => {
    if (!toggleMic()) toast("Micro non supporté ici (HTTPS requis)");
  };

  const placeholder = attachment
    ? "Ajoute un mot…"
    : variant === "chat"
      ? "Écris…"
      : "Écris un mot…";

  return (
    <div className={`composer-wrap${variant === "chat" ? " chat" : ""}`}>
      {attachment && (
        <div className="preview-bar">
          <div className="preview-wrap">
            <img className="preview-img" src={attachment.preview} alt="" />
            <button
              className="remove-btn"
              aria-label="Retirer la photo"
              type="button"
              onClick={() => setAttachment(null)}
            >
              <X size={10} strokeWidth={3} />
            </button>
          </div>
          <span className="preview-label">Photo jointe</span>
        </div>
      )}
      <div className="bar">
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          style={{ display: "none" }}
          onChange={handleFile}
        />
        <button
          className="icon-btn"
          title="Joindre une photo"
          type="button"
          onClick={() => fileRef.current?.click()}
        >
          <Paperclip size={17} />
        </button>
        <button
          className={`icon-btn${recording ? " recording" : ""}`}
          title="Dicter"
          type="button"
          onClick={handleMic}
        >
          <Mic size={17} />
        </button>
        <textarea
          ref={inputRef}
          className="msg-input"
          placeholder={placeholder}
          rows={1}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          className="send-btn"
          type="button"
          onClick={submit}
          disabled={!canSend}
        >
          <Send size={18} strokeWidth={2.5} />
        </button>
      </div>
    </div>
  );
}
