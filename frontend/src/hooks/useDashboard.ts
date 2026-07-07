// ── Hook de chargement du dashboard ──────────────────────────────────────────
// Charge GET /dashboard au montage puis rafraîchit toutes les 2 min (comme le
// setInterval de bot/static/js/main.js). Expose l'état pour le rendu des cards.
// Le refresh piloté par `refresh_cards` (réponses du bot) est branché aux
// steps chat/composer ; ici, boot + tick périodique suffisent.

import { useCallback, useEffect, useState } from "react";
import { apiGet, ApiError } from "../api/client";
import type { DashboardResponse } from "../api/types";

const REFRESH_MS = 120_000;

export interface UseDashboard {
  data: DashboardResponse | null;
  error: ApiError | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useDashboard(): UseDashboard {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const snapshot = await apiGet<DashboardResponse>("/dashboard");
      setData(snapshot);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError("Erreur réseau", 0));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return { data, error, loading, refresh };
}
