import { useMemo } from "react";
import { Button } from "@/components/ui/button";
import { MiniCurve, PrimaryCurve } from "@/lib/charts";
import type { Derivative } from "@/lib/types";

type Props = {
  derivatives: Derivative[];
  selectedDerivativeId: string | null;
  onSelectDerivative: (id: string) => void;
  onEditDerivative: (id: string) => void;
  onEditVolumes: (id: string) => void;
};

function derivativeCurve(derivative: Derivative) {
  const totals = new Map<string, number>();
  for (const volume of derivative.customervolumeList?.items || []) {
    for (const point of volume.customervolumecurvepointList?.items || []) {
      if (!point.volumeDate) continue;
      totals.set(point.volumeDate, (totals.get(point.volumeDate) || 0) + Number(point.volume || 0));
    }
  }
  return [...totals.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([date, value]) => ({ date, value }));
}

function derivativeVolumeMeta(derivative: Derivative) {
  const volumes = derivative.customervolumeList?.items || [];
  const sops = volumes.map((item) => item.sop).filter(Boolean).sort();
  const eops = volumes.map((item) => item.eop).filter(Boolean).sort();
  const points = volumes.reduce((count, volume) => count + (volume.customervolumecurvepointList?.items?.length || 0), 0);
  return {
    volumeCount: volumes.length,
    pointCount: points,
    earliestSop: sops[0] || "-",
    latestEop: eops[eops.length - 1] || "-",
  };
}

export function DerivativesSplitPanel({
  derivatives,
  selectedDerivativeId,
  onSelectDerivative,
  onEditDerivative,
  onEditVolumes,
}: Props) {
  const derivativeRows = useMemo(
    () =>
      derivatives.map((derivative) => ({
        derivative,
        curve: derivativeCurve(derivative),
        meta: derivativeVolumeMeta(derivative),
      })),
    [derivatives]
  );
  const selectedRow = useMemo(
    () => derivativeRows.find((item) => String(item.derivative.id) === String(selectedDerivativeId || "")) || derivativeRows[0] || null,
    [derivativeRows, selectedDerivativeId]
  );
  const selectedDerivative = selectedRow?.derivative || null;
  const selectedCurve = selectedRow?.curve.map((item) => ({ date: item.date, value: item.value })) || [];
  const selectedMeta = selectedRow?.meta || null;
  const sharedMax = useMemo(() => {
    const values = derivativeRows.flatMap((row) => row.curve.map((point) => Number(point.value || 0)));
    return Math.max(1, ...values);
  }, [derivativeRows]);

  if (!derivatives.length) {
    return <p className="text-sm text-muted-foreground">No derivatives on this project.</p>;
  }

  return (
    <div className="grid items-stretch gap-3 lg:grid-cols-[1.15fr_1fr]">
      <section className="flex h-[640px] flex-col overflow-hidden rounded-lg border border-border bg-white">
        <header className="grid grid-cols-[2fr_1fr_1fr_100px_140px] gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <span>Name</span>
          <span>Type</span>
          <span>Plant</span>
          <span>Volumes</span>
          <span>Curve</span>
        </header>
        <div className="flex-1 overflow-auto">
          {derivativeRows.map(({ derivative, meta, curve }) => {
            const isSelected = String(derivative.id) === String(selectedDerivative?.id || "");
            return (
              <button
                type="button"
                key={String(derivative.id)}
                className={`grid w-full grid-cols-[2fr_1fr_1fr_100px_140px] items-center gap-2 border-b border-border px-3 py-2 text-left text-sm transition ${
                  isSelected ? "bg-primary/10" : "hover:bg-muted/40"
                }`}
                onClick={() => onSelectDerivative(String(derivative.id))}
                aria-label={`Select derivative ${derivative.name || derivative.id}`}
              >
                <div>
                  <p className="font-medium">{derivative.name || "(unnamed)"}</p>
                  <p className="text-xs text-muted-foreground">#{derivative.id}</p>
                </div>
                <p className="truncate text-muted-foreground">{derivative.derivativeType?.name || "-"}</p>
                <p className="truncate text-muted-foreground">{derivative.Plant?.name || "-"}</p>
                <p className="text-muted-foreground">{meta.volumeCount}</p>
                <MiniCurve data={curve} yDomain={[0, sharedMax]} />
              </button>
            );
          })}
        </div>
      </section>

      <section className="flex h-[640px] flex-col rounded-lg border border-border bg-white p-3">
        {selectedDerivative ? (
          <>
            <div className="mb-3 border-b border-border pb-3">
              <p className="font-serif text-2xl">{selectedDerivative.name || "(unnamed derivative)"}</p>
              <p className="text-sm text-muted-foreground">#{selectedDerivative.id}</p>
            </div>

            <div className="grid flex-1 gap-3 overflow-auto pr-1 text-sm">
              <div className="rounded-md border border-border bg-muted/20 p-2">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Identity</p>
                <p><strong>Type:</strong> {selectedDerivative.derivativeType?.name || "-"}</p>
                <p><strong>Plant:</strong> {selectedDerivative.Plant?.name || "-"}</p>
              </div>

              <div className="rounded-md border border-border bg-muted/20 p-2">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Production</p>
                <p><strong>Pieces / Car Set:</strong> {selectedDerivative.piecesPerCarSet ?? "-"}</p>
                <p><strong>Norm Daily Quantity:</strong> {selectedDerivative.normDailyQuantity ?? "-"}</p>
                <p><strong>Max Daily Quantity:</strong> {selectedDerivative.maxDailyQuantity ?? "-"}</p>
              </div>

              <div className="rounded-md border border-border bg-muted/20 p-2">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Volume Summary</p>
                <p><strong>Volume Entries:</strong> {selectedMeta?.volumeCount ?? 0}</p>
                <p><strong>Curve Points:</strong> {selectedMeta?.pointCount ?? 0}</p>
                <p><strong>Earliest SOP:</strong> {selectedMeta?.earliestSop || "-"}</p>
                <p><strong>Latest EOP:</strong> {selectedMeta?.latestEop || "-"}</p>
              </div>

              <div className="rounded-md border border-border bg-muted/20 p-2">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Curve Preview</p>
                <PrimaryCurve data={selectedCurve} dataKey="value" showAxes />
              </div>

              <div className="rounded-md border border-border bg-muted/20 p-2">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Volume Description</p>
                <p className="text-muted-foreground">{selectedDerivative.volumeDescription || "No description set."}</p>
              </div>
            </div>

            <div className="mt-3 flex flex-wrap gap-2 border-t border-border pt-3">
              <Button onClick={() => onEditDerivative(String(selectedDerivative.id))}>Edit Derivative</Button>
              <Button variant="default" onClick={() => onEditVolumes(String(selectedDerivative.id))}>
                Edit Volumes
              </Button>
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Select a derivative to see details.</p>
        )}
      </section>
    </div>
  );
}
