// ── Toast (retour d'action bref, verre) ─────────────────────────────────────
// Portage de showToast() de bot/static/js/ui.js : un message éphémère centré au
// bas du viewport, auto-effacé après 1,8 s. Exposé via un contexte React pour
// que n'importe quel overlay/action puisse le déclencher (échec de cochage,
// de chargement…), comme la fonction globale showToast() du front vanilla.

import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";

// ReactNode (pas seulement string) : le toast d'action du composer porte une
// pastille icône + libellé (cf. actionToast du vanilla). Les appels existants
// avec une simple chaîne restent valides.
type ShowToast = (message: ReactNode) => void;

const ToastContext = createContext<ShowToast | null>(null);

/** Déclenche un toast. À utiliser sous `<ToastProvider>`. */
export function useToast(): ShowToast {
  const show = useContext(ToastContext);
  if (!show) throw new Error("useToast doit être utilisé dans un ToastProvider");
  return show;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<ReactNode>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = useCallback<ShowToast>((msg) => {
    if (timer.current) clearTimeout(timer.current);
    setMessage(msg);
    timer.current = setTimeout(() => setMessage(null), 1800);
  }, []);

  return (
    <ToastContext.Provider value={show}>
      {children}
      {message !== null && <div id="toast">{message}</div>}
    </ToastContext.Provider>
  );
}
