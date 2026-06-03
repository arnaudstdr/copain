// ── Wrappers réseau : /config, /ask, /ask/image, /ask/stream (SSE) ────────
// Tous les appels authentifiés portent le header X-API-Key (lecture vivante
// du binding API_KEY, renseigné au boot par main.js via setApiKey).
import { API_BASE, API_KEY } from "./state.js";

// Récupère la config publique servie par le backend (pas d'auth : /config
// n'est accessible que sur le réseau privé Tailscale).
export async function fetchConfig() {
  return await fetch("/config").then(r => r.json());
}

export async function callText(message) {
  const res = await fetch(`${API_BASE}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify({ message })
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return await res.json();
}

export async function callImage(message, att) {
  const res = await fetch(`${API_BASE}/ask/image`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify({ message: message || "", image_b64: att.b64, media_type: att.mediaType })
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return await res.json();
}

/**
 * Historique des bulles du mode dialogue (GET /history).
 * `beforeId` (optionnel) = curseur de pagination pour remonter dans le passé.
 * Renvoie { messages: [{id, role, content, created_at}], has_more }.
 */
export async function fetchHistory(limit = 50, beforeId = null) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (beforeId != null) params.set("before_id", String(beforeId));
  const res = await fetch(`${API_BASE}/history?${params}`, {
    headers: { "X-API-Key": API_KEY }
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return await res.json();
}

/**
 * Appel streamé de /ask/stream (SSE sur POST via fetch + ReadableStream).
 * `handlers` : { onDelta(text), onReplace(text), onDone(intent, refreshCards), onError(text) }.
 * Les frames sont de la forme `data: {json}\n\n` (cf. bot/api.py).
 */
export async function callTextStream(message, handlers) {
  const res = await fetch(`${API_BASE}/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify({ message })
  });
  if (!res.ok || !res.body) throw new Error(`${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const line = frame.split("\n").find(l => l.startsWith("data: "));
      if (!line) continue;
      let evt;
      try { evt = JSON.parse(line.slice(6)); } catch { continue; }
      if (evt.type === "delta") handlers.onDelta(evt.text || "");
      else if (evt.type === "replace") handlers.onReplace(evt.text || "");
      else if (evt.type === "done") handlers.onDone(evt.intent, evt.refresh_cards || []);
      else if (evt.type === "error") handlers.onError(evt.text || "");
    }
  }
}
