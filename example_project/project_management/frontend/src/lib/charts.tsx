import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { CurveSeriesPoint } from "@/lib/types";

function yearLabel(value: unknown) {
  const text = String(value || "");
  return /^\d{4}/.test(text) ? text.slice(0, 4) : text;
}

export function parseCurveJson(curveJson?: string | null): CurveSeriesPoint[] {
  if (!curveJson) return [];
  try {
    const parsed = JSON.parse(curveJson) as CurveSeriesPoint[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function MiniCurve({
  data,
  yDomain,
}: {
  data: Array<{ date: string; value: number }>;
  yDomain?: [number, number];
}) {
  if (!data.length) {
    return <p className="text-xs text-muted-foreground">No curve points.</p>;
  }
  return (
    <div className="mt-2 h-20 rounded-md border border-border bg-muted/30 p-1">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <XAxis dataKey="date" hide />
          <YAxis hide domain={yDomain || ["auto", "auto"]} />
          <Line type="monotone" dataKey="value" stroke="#0f7b6c" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function PrimaryCurve({
  data,
  dataKey,
  showAxes = false,
  yDomain,
}: {
  data: CurveSeriesPoint[];
  dataKey: "total_volume" | "used_volume" | "value";
  showAxes?: boolean;
  yDomain?: [number, number];
}) {
  if (!data.length) {
    return <p className="text-sm text-muted-foreground">No curve data available.</p>;
  }
  return (
    <div className="h-56 w-full rounded-md border border-border bg-muted/30 p-2">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <XAxis
            dataKey="date"
            hide={!showAxes}
            tick={{ fontSize: 11 }}
            axisLine={showAxes}
            tickLine={showAxes}
            tickFormatter={yearLabel}
            label={showAxes ? { value: "Year", position: "insideBottomRight", offset: -2 } : undefined}
          />
          <YAxis
            hide={!showAxes}
            tick={{ fontSize: 11 }}
            axisLine={showAxes}
            tickLine={showAxes}
            domain={yDomain || ["auto", "auto"]}
            label={showAxes ? { value: "Volume", angle: -90, position: "insideLeft" } : undefined}
          />
          <Tooltip labelFormatter={(value) => yearLabel(value)} />
          <Line type="monotone" dataKey={dataKey} stroke="#0f7b6c" strokeWidth={3} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
