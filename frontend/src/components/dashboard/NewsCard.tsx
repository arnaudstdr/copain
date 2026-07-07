// Card Actu (pleine largeur, fetch au tap). Gère idle / loading / data ; le
// fetch GET /news/latest et l'overlay markdown arrivent au step 05 — ici la
// card part en idle. Iso-visuel de newsCard() de dashboard.js.
import { Newspaper } from "lucide-react";
import { formatRelativeAge } from "../../lib/format";
import { Card, CardHead } from "./Card";

// État en mémoire de la card (persistant tant que la PWA est ouverte).
export interface NewsState {
  loading: boolean;
  markdown: string | null;
  fetchedAt: string | null;
}

const IDLE: NewsState = { loading: false, markdown: null, fetchedAt: null };

interface Props {
  news?: NewsState;
  onOpen: () => void;
}

export function NewsCard({ news = IDLE, onOpen }: Props) {
  let primary: string;
  let meta: string;

  if (news.loading) {
    primary = "Chargement…";
    meta = "Curation des dernières 24h";
  } else if (news.markdown) {
    primary = "Tape pour relire";
    const ago = news.fetchedAt ? formatRelativeAge(news.fetchedAt) : "";
    meta = ago ? `Mis à jour ${ago}` : "";
  } else {
    primary = "Tape pour les dernières actus";
    meta = "Curation IA des 24h";
  }

  return (
    <Card tappable empty={!news.loading && !news.markdown} onClick={onOpen}>
      <CardHead icon={Newspaper} label="Actu" />
      <div className="card-primary">{primary}</div>
      {meta && <div className="card-meta">{meta}</div>}
    </Card>
  );
}
