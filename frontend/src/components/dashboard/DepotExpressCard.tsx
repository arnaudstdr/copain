// Card « Dépôt express » (entrée décharge cognitive). Volontairement neutre et
// invitante, sans compteur. Iso-visuel de depotCard().
import { PenLine } from "lucide-react";
import { Card, CardHead } from "./Card";

interface Props {
  onOpen: () => void;
}

export function DepotExpressCard({ onOpen }: Props) {
  return (
    <Card compact tappable className="depot-card" onClick={onOpen}>
      <CardHead icon={PenLine} label="Dépôt express" />
      <div className="card-primary">Quelque chose en tête ?</div>
      <div className="card-meta">Vide-toi la tête, je garde</div>
    </Card>
  );
}
