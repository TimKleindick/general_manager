import { Button } from "@/components/ui/button";
import { MiniCurve } from "@/lib/charts";
import type { Derivative } from "@/lib/types";

type Props = {
  derivative: Derivative;
  onEditDerivative: (id: string) => void;
  onEditVolumes: (id: string) => void;
};

export function DerivativeRow({ derivative, onEditDerivative, onEditVolumes }: Props) {
  const totals = new Map<string, number>();
  for (const volume of derivative.customervolumeList?.items || []) {
    for (const point of volume.customervolumecurvepointList?.items || []) {
      const date = point.volumeDate;
      if (!date) continue;
      totals.set(date, (totals.get(date) || 0) + Number(point.volume || 0));
    }
  }
  const data = [...totals.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([date, value]) => ({ date, value }));

  return (
    <div className="rounded-md border border-border bg-white p-3">
      <p className="font-semibold">{derivative.name || "(unnamed)"}</p>
      <p className="text-sm text-muted-foreground">
        Type: {derivative.derivativeType?.name || "-"} · Plant: {derivative.Plant?.name || "-"} · Volumes: {(derivative.customervolumeList?.items || []).length}
      </p>
      <MiniCurve data={data} />
      <div className="mt-2 flex flex-wrap gap-2">
        <Button onClick={() => onEditDerivative(String(derivative.id))}>Edit Derivative</Button>
        <Button variant="default" onClick={() => onEditVolumes(String(derivative.id))}>Edit Volumes</Button>
      </div>
    </div>
  );
}
