import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Project } from "@/lib/types";

type Props = {
  project: Project;
  onOpen: (id: string) => void;
};

export function ProjectCard({ project, onOpen }: Props) {
  const nominationValue = project.probabilityOfNomination?.value;
  const nomination =
    typeof nominationValue === "number" && !Number.isNaN(nominationValue)
      ? `${Math.round(nominationValue)}%`
      : "-";

  return (
    <Card className="border-border/80 bg-white/90">
      <CardHeader>
        <div>
          <CardTitle className="text-base">#{project.id} {project.name || "(no name)"}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            {project.customer?.companyName || "-"} ({project.customer?.groupName || "-"})
          </p>
        </div>
        <Badge>{project.projectPhaseType?.name || "n/a"}</Badge>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-2 text-sm text-muted-foreground md:grid-cols-5">
          <div><strong>Total:</strong> {Number(project.totalVolume || 0).toLocaleString()}</div>
          <div><strong>Nomination:</strong> {nomination}</div>
          <div><strong>SOP:</strong> {project.earliestSop || "-"}</div>
          <div><strong>EOP:</strong> {project.latestEop || "-"}</div>
          <div className="md:text-right">
            <Button variant="default" onClick={() => onOpen(String(project.id))}>Open Dashboard</Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
