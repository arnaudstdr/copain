// ── État + fetch de la card Actu ─────────────────────────────────────────────
// Canal pull : GET /news/latest au 1er tap seulement, résultat mémorisé tant que
// la PWA est ouverte (réouverture = pas de refetch). Portage de openNews() de
// bot/static/js/dashboard.js. Toast d'erreur (comme le vanilla).

import { useCallback, useState } from "react";
import { apiGet } from "../api/client";
import type { NewsLatestResponse } from "../api/types";
import type { NewsState } from "../components/dashboard/NewsCard";
import { useToast } from "../components/Toast";

const IDLE: NewsState = { loading: false, markdown: null, fetchedAt: null };

export function useNews() {
  const toast = useToast();
  const [news, setNews] = useState<NewsState>(IDLE);
  // Régénération forcée en cours (bouton « Actualiser » de l'overlay). Séparé de
  // `news.loading` pour ne pas repasser la card en état « Chargement… ».
  const [refreshing, setRefreshing] = useState(false);

  // `onReady` ouvre la vue markdown (piloté par l'état d'overlay de App).
  const open = useCallback(
    async (onReady: () => void) => {
      if (news.markdown && !news.loading) {
        onReady();
        return;
      }
      if (news.loading) return;
      setNews((prev) => ({ ...prev, loading: true }));
      try {
        const data = await apiGet<NewsLatestResponse>("/news/latest");
        setNews({ loading: false, markdown: data.markdown, fetchedAt: data.fetched_at });
        onReady();
      } catch {
        setNews((prev) => ({ ...prev, loading: false }));
        toast("Impossible de charger les actus");
      }
    },
    [news.markdown, news.loading, toast],
  );

  // Force une régénération serveur (?refresh=true) sans fermer l'overlay. Garde
  // anti-réentrance ; en cas d'échec le digest affiché reste en place.
  const refresh = useCallback(async () => {
    if (news.loading || refreshing) return;
    setRefreshing(true);
    try {
      const data = await apiGet<NewsLatestResponse>("/news/latest?refresh=true");
      setNews({ loading: false, markdown: data.markdown, fetchedAt: data.fetched_at });
    } catch {
      toast("Impossible de charger les actus");
    } finally {
      setRefreshing(false);
    }
  }, [news.loading, refreshing, toast]);

  return { news, open, refresh, refreshing };
}
