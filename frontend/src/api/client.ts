// ── Client HTTP ─────────────────────────────────────────────────────────────
// Wrapper fetch à base URL relative (same-origin) : en prod le front et l'API
// sont servis par le même FastAPI ; en dev le proxy Vite relaie vers :8000.
// La clé `X-API-Key` est récupérée une fois au boot via GET /config (comme la
// PWA vanilla actuelle), gardée en mémoire, puis injectée sur chaque appel
// authentifié. Aucun changement backend (Décision 7 du SPEC).

import type {
  Action,
  AskResponse,
  AskStreamRequest,
  ConfigResponse,
  StreamHandlers,
} from "./types";

const DEFAULT_TIMEOUT_MS = 30_000;
// /ask et /ask/image appellent le LLM en un bloc (pas de streaming) ; la vision
// multimodale peut largement dépasser le timeout par défaut. On leur laisse une
// marge confortable pour ne pas couper une réponse lente (le vanilla n'avait
// aucun timeout sur ces appels).
const ASK_TIMEOUT_MS = 120_000;

// Clé mémorisée en mémoire du module (jamais persistée). Bindings vivants.
let apiKey = "";
// Promesse mémoïsée du GET /config : le fetch n'a lieu qu'une fois même si
// plusieurs appels concurrents le déclenchent au boot.
let configPromise: Promise<string> | null = null;

/** Erreur HTTP typée : statut non-2xx ou échec réseau/timeout. */
export class ApiError extends Error {
  readonly status: number; // 0 = pas de réponse (réseau / timeout / abort)
  readonly isTimeout: boolean;

  constructor(message: string, status: number, isTimeout = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.isTimeout = isTimeout;
  }
}

/** Récupère la config publique (pas d'auth : /config est privé au réseau Tailscale). */
export async function fetchConfig(): Promise<ConfigResponse> {
  const res = await fetch("/config");
  if (!res.ok) {
    throw new ApiError(`GET /config a échoué (${res.status})`, res.status);
  }
  return (await res.json()) as ConfigResponse;
}

/**
 * Garantit que la clé API est chargée (memoïsée). Appelée paresseusement avant
 * chaque requête authentifiée ; peut aussi être déclenchée au boot pour
 * préchauffer (voir `bootstrapConfig`).
 */
export async function ensureApiKey(): Promise<string> {
  if (apiKey) return apiKey;
  if (!configPromise) {
    configPromise = fetchConfig()
      .then((cfg) => {
        apiKey = cfg.api_key;
        return apiKey;
      })
      .catch((err) => {
        // Ne pas figer l'échec : un prochain appel pourra réessayer.
        configPromise = null;
        throw err;
      });
  }
  return configPromise;
}

/** Précharge la clé au démarrage de l'app (best-effort, appelé depuis main.tsx). */
export async function bootstrapConfig(): Promise<void> {
  // Best-effort : si le tunnel Tailscale n'est pas encore monté au boot, le GET
  // /config échoue et `ensureApiKey` réessaiera paresseusement au 1er appel
  // authentifié (configPromise remis à null). On avale donc le rejet ici pour
  // tenir le contrat « best-effort » et éviter une unhandled rejection au boot.
  try {
    await ensureApiKey();
  } catch {
    // silencieux : retry paresseux garanti par ensureApiKey
  }
}

// Combine un timeout interne et un éventuel signal externe en un seul signal.
function withTimeout(
  timeoutMs: number,
  external?: AbortSignal,
): { signal: AbortSignal; clear: () => void; didTimeout: () => boolean } {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onExternalAbort = () => controller.abort();
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener("abort", onExternalAbort, { once: true });
  }
  return {
    signal: controller.signal,
    clear: () => {
      clearTimeout(timer);
      external?.removeEventListener("abort", onExternalAbort);
    },
    didTimeout: () => timedOut,
  };
}

interface RequestOptions {
  timeoutMs?: number;
  signal?: AbortSignal;
}

/** Requête authentifiée typée : injecte X-API-Key, gère timeout + erreurs non-2xx. */
async function apiFetch<T>(path: string, init: RequestInit, opts: RequestOptions = {}): Promise<T> {
  const key = await ensureApiKey();
  const { signal, clear, didTimeout } = withTimeout(opts.timeoutMs ?? DEFAULT_TIMEOUT_MS, opts.signal);
  try {
    const res = await fetch(path, {
      ...init,
      signal,
      headers: { ...(init.headers ?? {}), "X-API-Key": key },
    });
    if (!res.ok) {
      // Remonte le message FR du backend (FastAPI : { "detail": "..." }) plutôt
      // qu'un code brut, pour que les toasts d'erreur soient parlants. Fail-soft :
      // corps non-JSON, ou `detail` non-textuel (422 = liste) → message technique.
      let detail = "";
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body?.detail === "string") detail = body.detail;
      } catch {
        // corps illisible : on garde le message technique ci-dessous
      }
      throw new ApiError(detail || `${init.method ?? "GET"} ${path} → ${res.status}`, res.status);
    }
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (didTimeout()) {
      throw new ApiError(`${init.method ?? "GET"} ${path} a expiré`, 0, true);
    }
    throw new ApiError(err instanceof Error ? err.message : "Erreur réseau", 0);
  } finally {
    clear();
  }
}

/** GET typé sur un endpoint authentifié. */
export function apiGet<T>(path: string, opts?: RequestOptions): Promise<T> {
  return apiFetch<T>(path, { method: "GET" }, opts);
}

/** POST JSON typé sur un endpoint authentifié. */
export function apiPost<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T> {
  return apiFetch<T>(
    path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    },
    opts,
  );
}

/** GET authentifié renvoyant un binaire (ex. export CSV → téléchargement blob). */
export async function apiGetBlob(path: string, opts: RequestOptions = {}): Promise<Blob> {
  const key = await ensureApiKey();
  const { signal, clear, didTimeout } = withTimeout(opts.timeoutMs ?? DEFAULT_TIMEOUT_MS, opts.signal);
  try {
    const res = await fetch(path, { method: "GET", signal, headers: { "X-API-Key": key } });
    if (!res.ok) throw new ApiError(`GET ${path} → ${res.status}`, res.status);
    return await res.blob();
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (didTimeout()) throw new ApiError(`GET ${path} a expiré`, 0, true);
    throw new ApiError(err instanceof Error ? err.message : "Erreur réseau", 0);
  } finally {
    clear();
  }
}

/** DELETE typé sur un endpoint authentifié (réponse ignorée). */
export async function apiDelete(path: string, opts: RequestOptions = {}): Promise<void> {
  const key = await ensureApiKey();
  const { signal, clear, didTimeout } = withTimeout(opts.timeoutMs ?? DEFAULT_TIMEOUT_MS, opts.signal);
  try {
    const res = await fetch(path, { method: "DELETE", signal, headers: { "X-API-Key": key } });
    if (!res.ok) throw new ApiError(`DELETE ${path} → ${res.status}`, res.status);
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (didTimeout()) throw new ApiError(`DELETE ${path} a expiré`, 0, true);
    throw new ApiError(err instanceof Error ? err.message : "Erreur réseau", 0);
  } finally {
    clear();
  }
}

/**
 * Envoi d'une photo (POST /ask/image) : image base64 SANS le préfixe
 * `data:...;base64,` (le backend décode avec `validate=True`). Le LLM
 * multimodal traite légende + image en un seul appel. Portage de `callImage`.
 */
export function askImage(
  message: string,
  imageB64: string,
  mediaType: string,
): Promise<AskResponse> {
  return apiPost<AskResponse>(
    "/ask/image",
    { message: message || "", image_b64: imageB64, media_type: mediaType },
    { timeoutMs: ASK_TIMEOUT_MS },
  );
}

/**
 * Appel streamé de POST /ask/stream (SSE sur POST via fetch + ReadableStream ;
 * EventSource ne supporte pas les headers custom → obligation de passer par
 * fetch pour porter X-API-Key). Parse les frames `data: {json}\n\n`, même
 * coupées entre deux chunks. Consommé au step 07 (mode dialogue).
 */
export async function streamAsk(
  message: string,
  handlers: StreamHandlers,
  think = false,
  signal?: AbortSignal,
): Promise<void> {
  const key = await ensureApiKey();
  const res = await fetch("/ask/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": key },
    body: JSON.stringify({ message, think } satisfies AskStreamRequest),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new ApiError(`POST /ask/stream → ${res.status}`, res.status);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      let evt: {
        type?: string;
        text?: string;
        intent?: string;
        refresh_cards?: string[];
        actions?: Action[];
      };
      try {
        evt = JSON.parse(line.slice(6));
      } catch {
        continue;
      }
      if (evt.type === "delta") handlers.onDelta(evt.text ?? "");
      else if (evt.type === "replace") handlers.onReplace(evt.text ?? "");
      else if (evt.type === "done")
        handlers.onDone(evt.intent ?? "answer", evt.refresh_cards ?? [], evt.actions ?? []);
      else if (evt.type === "error") handlers.onError(evt.text ?? "");
    }
  }
}
