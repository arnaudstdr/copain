// Card « Pour toi » (sortie restitution des dépôts). Neutre : aucun badge ni
// compteur entrant (positionnement produit anti-charge mentale). Les états
// idle/loading/data vivent dans l'overlay (step 06). Iso-visuel de foryouCard().
import { Inbox } from "lucide-react";
import { Card, CardHead } from "./Card";

interface Props {
  onOpen: () => void;
}

export function ForYouCard({ onOpen }: Props) {
  return (
    <Card compact empty tappable onClick={onOpen}>
      <CardHead icon={Inbox} label="Pour toi" />
      <div className="card-primary">Tape pour faire le point</div>
      <div className="card-meta">Tes dépôts, remis en perspective</div>
    </Card>
  );
}
