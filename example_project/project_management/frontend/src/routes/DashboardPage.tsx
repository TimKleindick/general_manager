import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "@/store";
import { fetchCatalogs, fetchDashboardProject, mutate } from "@/store/thunks";
import {
  closeModal,
  openModal,
  setProjectId,
  setSelectedDerivativeId,
  setSelectedVolumeId,
  setVolumeModalDerivativeId,
} from "@/store/slices/dashboardSlice";
import { pushNotification, setRouteMode, setWsStatus } from "@/store/slices/appSlice";
import type { CurvePoint, Derivative, Project, Volume } from "@/lib/types";
import { parseCurveJson, PrimaryCurve } from "@/lib/charts";
import { createSubscriptionClient } from "@/lib/subscriptions";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Table, TBody, TD, TH, THead } from "@/components/ui/table";
import { DerivativesSplitPanel } from "@/components/dashboard/DerivativesSplitPanel";

const MUTATIONS = {
  updateProject: `mutation UpdateProject($id: Int!, $name: String!, $customer: ID!, $projectPhaseType: ID!, $projectType: ID, $currency: ID!, $probabilityOfNomination: MeasurementScalar) { updateProject(id: $id, name: $name, customer: $customer, projectPhaseType: $projectPhaseType, projectType: $projectType, currency: $currency, probabilityOfNomination: $probabilityOfNomination) { success } }`,
  updateCustomer: `mutation UpdateCustomer($id: Int!, $companyName: String!, $groupName: String!, $number: Int, $keyAccount: ID) { updateCustomer(id: $id, companyName: $companyName, groupName: $groupName, number: $number, keyAccount: $keyAccount) { success } }`,
  updateDerivative: `mutation UpdateDerivative($id: Int!, $project: ID!, $name: String!, $derivativeType: ID!, $Plant: ID!, $piecesPerCarSet: Int, $normDailyQuantity: Int, $volumeDescription: String) { updateDerivative(id: $id, project: $project, name: $name, derivativeType: $derivativeType, Plant: $Plant, piecesPerCarSet: $piecesPerCarSet, normDailyQuantity: $normDailyQuantity, volumeDescription: $volumeDescription) { success } }`,
  updateVolume: `mutation UpdateVolume($id: Int!, $derivative: ID!, $projectPhaseType: ID, $sop: Date!, $eop: Date!, $description: String, $usedVolume: Boolean, $isVolumeInVehicles: Boolean) { updateCustomerVolume(id: $id, derivative: $derivative, projectPhaseType: $projectPhaseType, sop: $sop, eop: $eop, description: $description, usedVolume: $usedVolume, isVolumeInVehicles: $isVolumeInVehicles) { success } }`,
  createCurvePoint: `mutation CreateCurvePoint($customerVolume: ID!, $volumeDate: Date!, $volume: Int!) { createCustomerVolumeCurvePoint(customerVolume: $customerVolume, volumeDate: $volumeDate, volume: $volume) { success } }`,
  updateCurvePoint: `mutation UpdateCurvePoint($id: Int!, $customerVolume: ID!, $volumeDate: Date!, $volume: Int!) { updateCustomerVolumeCurvePoint(id: $id, customerVolume: $customerVolume, volumeDate: $volumeDate, volume: $volume) { success } }`,
  deleteCurvePoint: `mutation DeleteCurvePoint($id: Int!) { deleteCustomerVolumeCurvePoint(id: $id) { success } }`,
  createProjectTeam: `mutation CreateProjectTeam($project: ID!, $projectUserRole: ID!, $responsibleUser: ID!, $active: Boolean) { createProjectTeam(project: $project, projectUserRole: $projectUserRole, responsibleUser: $responsibleUser, active: $active) { success } }`,
  updateProjectTeam: `mutation UpdateProjectTeam($id: Int!, $project: ID!, $projectUserRole: ID!, $responsibleUser: ID!, $active: Boolean) { updateProjectTeam(id: $id, project: $project, projectUserRole: $projectUserRole, responsibleUser: $responsibleUser, active: $active) { success } }`,
  deleteProjectTeam: `mutation DeleteProjectTeam($id: Int!) { deleteProjectTeam(id: $id) { success } }`,
};

type EditableCurvePoint = {
  id: string | null;
  volumeDate: string;
  volume: number;
};

function yearStartDate(value: string) {
  if (!value) return "";
  const year = value.slice(0, 4);
  if (!/^\d{4}$/.test(year)) return "";
  return `${year}-01-01`;
}

function requireValue(value: string, msg: string) {
  if (String(value || "").trim()) return null;
  return msg;
}

function percentValue(value: unknown): number | null {
  if (!value || typeof value !== "object") return null;
  const maybeValue = (value as { value?: unknown }).value;
  if (typeof maybeValue !== "number" || Number.isNaN(maybeValue)) return null;
  return maybeValue;
}

function formatPercent(value: unknown): string {
  const parsed = percentValue(value);
  return parsed == null ? "-" : `${Math.round(parsed)}%`;
}

function derivatives(project: Project | null) {
  return project?.derivativeList?.items || [];
}

function allVolumes(project: Project | null) {
  const result: Array<Volume & { derivativeId: string; derivativeName: string; curvePoints: CurvePoint[] }> = [];
  for (const derivative of derivatives(project)) {
    for (const volume of derivative.customervolumeList?.items || []) {
      result.push({
        ...volume,
        derivativeId: String(derivative.id),
        derivativeName: derivative.name,
        curvePoints: volume.customervolumecurvepointList?.items || [],
      });
    }
  }
  return result;
}

export function DashboardPage() {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { project, curveJson, projectId, selectedDerivativeId, selectedVolumeId, volumeModalDerivativeId, modals } = useAppSelector((s) => s.dashboard);
  const wsStatus = useAppSelector((s) => s.app.wsStatus);
  const entities = useAppSelector((s) => s.entities);

  const [projectForm, setProjectForm] = useState({
    name: "",
    customer: "",
    projectPhaseType: "",
    projectType: "",
    currency: "",
    probabilityOfNomination: "",
  });
  const [customerForm, setCustomerForm] = useState({ companyName: "", groupName: "", number: "", keyAccount: "" });
  const [derivativeForm, setDerivativeForm] = useState({ name: "", project: "", derivativeType: "", plant: "", pieces: "", norm: "", description: "" });
  const [volumeForm, setVolumeForm] = useState({ derivative: "", phaseType: "", sop: "", eop: "", description: "", used: false, vehicles: false });
  const [curveRows, setCurveRows] = useState<EditableCurvePoint[]>([]);
  const [teamAssignments, setTeamAssignments] = useState<Record<string, string>>({});
  const [teamSaving, setTeamSaving] = useState(false);
  const routeProjectId = searchParams.get("projectId");

  useEffect(() => {
    dispatch(setRouteMode("dashboard"));
    if (!routeProjectId) {
      navigate("/projects/", { replace: true });
      return;
    }
    dispatch(setProjectId(routeProjectId));
    void dispatch(fetchCatalogs());
    void dispatch(fetchDashboardProject(routeProjectId));

    const client = createSubscriptionClient({
      onStatus(status) {
        dispatch(setWsStatus(status));
      },
    });
    client.connect();

    const handles: Array<{ unsubscribe: () => void }> = [];

    handles.push(
      client.subscribe({
        query: "subscription OnProject($id: ID!) { onProjectChange(id: $id) { action item { id } } }",
        variables: { id: routeProjectId },
        onNext: (data) => {
          const payload = data?.onProjectChange as { action?: string; item?: { id?: string | number } } | undefined;
          if (!payload || payload.action === "snapshot") return;
          dispatch(
            pushNotification({
              event: payload.action || "changed",
              entityType: "Project",
              entityId: String(payload.item?.id ?? routeProjectId),
              message: "Live subscription event",
            })
          );
          void dispatch(fetchDashboardProject(routeProjectId));
        },
      })
    );

    return () => {
      handles.forEach((handle) => handle.unsubscribe());
      client.close();
    };
  }, [dispatch, navigate, routeProjectId]);

  const derivativeItems = useMemo(() => derivatives(project), [project]);
  const selectedDerivative = useMemo(
    () => derivativeItems.find((item) => String(item.id) === String(selectedDerivativeId || "")) || derivativeItems[0] || null,
    [derivativeItems, selectedDerivativeId]
  );

  const volumeItems = useMemo(() => {
    const all = allVolumes(project);
    if (!volumeModalDerivativeId) return all;
    return all.filter((item) => String(item.derivativeId) === String(volumeModalDerivativeId));
  }, [project, volumeModalDerivativeId]);

  const selectedVolume = useMemo(
    () => volumeItems.find((item) => String(item.id) === String(selectedVolumeId || "")) || volumeItems[0] || null,
    [volumeItems, selectedVolumeId]
  );

  useEffect(() => {
    if (!project) return;
    setProjectForm({
      name: project.name || "",
      customer: String(project.customer?.id || ""),
      projectPhaseType: String(project.projectPhaseType?.id || ""),
      projectType: String(project.projectType?.id || ""),
      currency: String(project.currency?.id || ""),
      probabilityOfNomination: String(percentValue(project.probabilityOfNomination) ?? ""),
    });
    setCustomerForm({
      companyName: project.customer?.companyName || "",
      groupName: project.customer?.groupName || "",
      number: String(project.customer?.number ?? ""),
      keyAccount: String(project.customer?.keyAccount?.id || ""),
    });
  }, [project]);

  useEffect(() => {
    if (!selectedDerivative) return;
    setDerivativeForm({
      name: selectedDerivative.name || "",
      project: String(project?.id || ""),
      derivativeType: String(selectedDerivative.derivativeType?.id || ""),
      plant: String(selectedDerivative.Plant?.id || ""),
      pieces: String(selectedDerivative.piecesPerCarSet ?? ""),
      norm: String(selectedDerivative.normDailyQuantity ?? ""),
      description: selectedDerivative.volumeDescription || "",
    });
  }, [selectedDerivative, project?.id]);

  useEffect(() => {
    if (!selectedVolume) return;
    setVolumeForm({
      derivative: String(selectedVolume.derivativeId || ""),
      phaseType: String(selectedVolume.projectPhaseType?.id || ""),
      sop: selectedVolume.sop || "",
      eop: selectedVolume.eop || "",
      description: selectedVolume.description || "",
      used: Boolean(selectedVolume.usedVolume),
      vehicles: Boolean(selectedVolume.isVolumeInVehicles),
    });
    setCurveRows((selectedVolume.curvePoints || []).map((point) => ({ id: String(point.id), volumeDate: point.volumeDate, volume: Number(point.volume || 0) })));
  }, [selectedVolume]);

  const projectCurve = useMemo(() => parseCurveJson(curveJson), [curveJson]);
  const modalCurve = useMemo(() => curveRows.map((row) => ({ date: row.volumeDate, value: Number(row.volume || 0) })), [curveRows]);

  const runMutationAndRefresh = async (
    mutation: string,
    variables: Record<string, unknown>,
    event: string,
    entityType: string,
    entityId: string,
    message?: string
  ) => {
    if (!projectId) return;
    await dispatch(mutate({ mutation, variables })).unwrap();
    dispatch(pushNotification({ event, entityType, entityId, message }));
    await dispatch(fetchDashboardProject(projectId)).unwrap();
  };

  const onUpdateProject = async () => {
    const missing =
      requireValue(projectForm.name, "Project name is required") ||
      requireValue(projectForm.customer, "Project customer is required") ||
      requireValue(projectForm.projectPhaseType, "Project phase type is required") ||
      requireValue(projectForm.currency, "Project currency is required");
    if (missing || !projectId) return alert(missing);
    await runMutationAndRefresh(MUTATIONS.updateProject, {
      id: Number(projectId),
      name: projectForm.name,
      customer: projectForm.customer,
      projectPhaseType: projectForm.projectPhaseType,
      projectType: projectForm.projectType || null,
      currency: projectForm.currency,
      probabilityOfNomination: projectForm.probabilityOfNomination
        ? `${Number(projectForm.probabilityOfNomination)} percent`
        : null,
    }, "updated", "Project", String(projectId), "Project data saved");
    dispatch(closeModal("project"));
  };

  const onUpdateCustomer = async () => {
    const missing = requireValue(customerForm.companyName, "Company name is required") || requireValue(customerForm.groupName, "Group name is required");
    if (missing || !project?.customer?.id) return alert(missing);
    await runMutationAndRefresh(MUTATIONS.updateCustomer, {
      id: Number(project.customer.id),
      companyName: customerForm.companyName,
      groupName: customerForm.groupName,
      number: customerForm.number ? Number(customerForm.number) : null,
      keyAccount: customerForm.keyAccount || null,
    }, "updated", "Customer", String(project.customer.id), "Customer data saved");
    dispatch(closeModal("customer"));
  };

  const onUpdateDerivative = async () => {
    if (!selectedDerivative || !projectId) return;
    const missing =
      requireValue(derivativeForm.name, "Derivative name is required") ||
      requireValue(derivativeForm.derivativeType, "Derivative type is required") ||
      requireValue(derivativeForm.plant, "Derivative plant is required");
    if (missing) return alert(missing);
    await runMutationAndRefresh(MUTATIONS.updateDerivative, {
      id: Number(selectedDerivative.id),
      project: String(projectId),
      name: derivativeForm.name,
      derivativeType: derivativeForm.derivativeType,
      Plant: derivativeForm.plant,
      piecesPerCarSet: Number(derivativeForm.pieces || 1),
      normDailyQuantity: Number(derivativeForm.norm || 0),
      volumeDescription: derivativeForm.description || null,
    }, "updated", "Derivative", String(selectedDerivative.id), "Derivative data saved");
    dispatch(closeModal("derivative"));
  };

  const onUpdateVolume = async () => {
    if (!selectedVolume) return;
    const missing =
      requireValue(volumeForm.derivative, "Volume derivative is required") ||
      requireValue(volumeForm.sop, "Volume SOP is required") ||
      requireValue(volumeForm.eop, "Volume EOP is required");
    if (missing) return alert(missing);
    await runMutationAndRefresh(MUTATIONS.updateVolume, {
      id: Number(selectedVolume.id),
      derivative: volumeForm.derivative,
      projectPhaseType: volumeForm.phaseType || null,
      sop: volumeForm.sop,
      eop: volumeForm.eop,
      description: volumeForm.description || null,
      usedVolume: volumeForm.used,
      isVolumeInVehicles: volumeForm.vehicles,
    }, "updated", "Volume", String(selectedVolume.id), "Volume data saved");
  };

  const onSaveCurveRow = async (row: EditableCurvePoint) => {
    if (!selectedVolume) return;
    if (!row.volumeDate) return alert("Curve point date is required");
    const normalizedDate = yearStartDate(row.volumeDate);
    if (!normalizedDate || !normalizedDate.endsWith("-01-01")) {
      return alert("Curve point date must be January 1st of a year (YYYY-01-01).");
    }
    if (row.id) {
      await runMutationAndRefresh(MUTATIONS.updateCurvePoint, {
        id: Number(row.id),
        customerVolume: String(selectedVolume.id),
        volumeDate: normalizedDate,
        volume: Number(row.volume || 0),
      }, "updated", "CurvePoint", String(row.id), "Curve point saved");
    } else {
      await runMutationAndRefresh(MUTATIONS.createCurvePoint, {
        customerVolume: String(selectedVolume.id),
        volumeDate: normalizedDate,
        volume: Number(row.volume || 0),
      }, "created", "CurvePoint", "new", "Curve point created");
    }
  };

  const onDeleteCurveRow = async (row: EditableCurvePoint) => {
    if (!row.id) {
      setCurveRows((prev) => prev.filter((item) => item !== row));
      return;
    }
    if (!window.confirm(`Delete curve point #${row.id}?`)) return;
    await runMutationAndRefresh(
      MUTATIONS.deleteCurvePoint,
      { id: Number(row.id) },
      "deleted",
      "CurvePoint",
      String(row.id),
      "Curve point deleted"
    );
  };

  const onSaveAllRows = async () => {
    for (const row of curveRows) {
      await onSaveCurveRow(row);
    }
    dispatch(
      pushNotification({
        event: "saved",
        entityType: "CurvePoint",
        entityId: selectedVolume ? String(selectedVolume.id) : "-",
        message: "All visible curve rows saved",
      })
    );
  };

  const projectTotals = useMemo(() => {
    const nomination = formatPercent(project?.probabilityOfNomination);
    return {
      nomination,
      derivativeCount: derivatives(project).length,
      volumeCount: allVolumes(project).length,
    };
  }, [project]);

  const teamRoleRows = useMemo(() => {
    const activeAssignments = (project?.projectteamList?.items || []).filter((item) => item.active);
    const roles = entities.projectUserRoles.length
      ? entities.projectUserRoles
      : activeAssignments
          .map((item) => item.projectUserRole)
          .filter((item): item is { id: string | number; name?: string } => Boolean(item));

    return roles.map((role) => {
      const matches = activeAssignments.filter(
        (assignment) => String(assignment.projectUserRole?.id || "") === String(role.id)
      );
      const assignees = matches
        .map((assignment) => assignment.responsibleUser?.fullName || assignment.responsibleUser?.username || "")
        .filter(Boolean);
      return {
        roleId: String(role.id),
        roleName: role.name || `Role #${role.id}`,
        assignees,
        currentTeamId: matches[0]?.id ? String(matches[0].id) : null,
        currentUserId: matches[0]?.responsibleUser?.id ? String(matches[0].responsibleUser.id) : "",
      };
    });
  }, [entities.projectUserRoles, project?.projectteamList?.items]);

  useEffect(() => {
    const draft: Record<string, string> = {};
    for (const row of teamRoleRows) {
      draft[row.roleId] = row.currentUserId || "";
    }
    setTeamAssignments(draft);
  }, [teamRoleRows]);

  const onSaveTeamAssignments = async () => {
    if (!projectId) return;
    setTeamSaving(true);
    try {
      let operationCount = 0;
      for (const row of teamRoleRows) {
        const targetUserId = String(teamAssignments[row.roleId] || "");
        const currentUserId = String(row.currentUserId || "");
        if (targetUserId === currentUserId) continue;

        if (!targetUserId && row.currentTeamId) {
          await dispatch(
            mutate({
              mutation: MUTATIONS.deleteProjectTeam,
              variables: { id: Number(row.currentTeamId) },
            })
          ).unwrap();
          operationCount += 1;
          continue;
        }

        if (targetUserId && row.currentTeamId) {
          await dispatch(
            mutate({
              mutation: MUTATIONS.updateProjectTeam,
              variables: {
                id: Number(row.currentTeamId),
                project: String(projectId),
                projectUserRole: row.roleId,
                responsibleUser: targetUserId,
                active: true,
              },
            })
          ).unwrap();
          operationCount += 1;
          continue;
        }

        if (targetUserId && !row.currentTeamId) {
          await dispatch(
            mutate({
              mutation: MUTATIONS.createProjectTeam,
              variables: {
                project: String(projectId),
                projectUserRole: row.roleId,
                responsibleUser: targetUserId,
                active: true,
              },
            })
          ).unwrap();
          operationCount += 1;
        }
      }
      dispatch(
        pushNotification({
          event: "updated",
          entityType: "ProjectTeam",
          entityId: String(projectId),
          message: operationCount ? "Project team assignments saved" : "No team changes detected",
        })
      );
      await dispatch(fetchDashboardProject(projectId)).unwrap();
      dispatch(closeModal("team"));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Team update failed";
      dispatch(
        pushNotification({
          event: "error",
          entityType: "ProjectTeam",
          entityId: String(projectId),
          message,
        })
      );
      window.alert(`Could not save team changes: ${message}`);
    } finally {
      setTeamSaving(false);
    }
  };

  return (
    <main className="mx-auto max-w-7xl p-4 pb-16">
      <header className="mb-3 rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-serif text-3xl">Program Dashboard</h1>
            <p className="mt-1 text-sm text-muted-foreground">#{project?.id || projectId} {project?.name || "Loading project..."}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => navigate("/projects/?restore=1")}>Back to Project Selection</Button>
            <Badge>WS: {wsStatus}</Badge>
            <Badge>Project: #{project?.id || "-"}</Badge>
            <Badge>Customer: {project?.customer?.companyName || "-"}</Badge>
          </div>
        </div>
      </header>

      <section className="grid gap-3 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Project Overview</CardTitle>
            <Button onClick={() => dispatch(openModal("project"))}>Edit</Button>
          </CardHeader>
          <CardContent>
            <div className="grid gap-2 text-sm md:grid-cols-2">
              <p><strong>Phase:</strong> {project?.projectPhaseType?.name || "-"}</p>
              <p><strong>Type:</strong> {project?.projectType?.name || "-"}</p>
              <p><strong>Currency:</strong> {project?.currency?.abbreviation || project?.currency?.name || "-"}</p>
              <p><strong>Nomination:</strong> {projectTotals.nomination}</p>
              <p><strong>Total Volume:</strong> {Number(project?.totalVolume || 0).toLocaleString()}</p>
              <p><strong>Volume Flex:</strong> {formatPercent(project?.customerVolumeFlex)}</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Customer</CardTitle>
            <Button onClick={() => dispatch(openModal("customer"))}>Edit</Button>
          </CardHeader>
          <CardContent>
            <div className="grid gap-2 text-sm md:grid-cols-2">
              <p><strong>Company:</strong> {project?.customer?.companyName || "-"}</p>
              <p><strong>Group:</strong> {project?.customer?.groupName || "-"}</p>
              <p><strong>Number:</strong> {project?.customer?.number ?? "-"}</p>
              <p><strong>Key Account:</strong> {project?.customer?.keyAccount?.fullName || project?.customer?.keyAccount?.username || "-"}</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Volume Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="mb-2 grid gap-2 text-sm md:grid-cols-2">
              <p><strong>Derivatives:</strong> {projectTotals.derivativeCount}</p>
              <p><strong>Volume Entries:</strong> {projectTotals.volumeCount}</p>
              <p><strong>Earliest SOP:</strong> {project?.earliestSop || "-"}</p>
              <p><strong>Latest EOP:</strong> {project?.latestEop || "-"}</p>
            </div>
            <PrimaryCurve
              data={projectCurve}
              dataKey={projectCurve.some((item) => Number(item.total_volume || 0) > 0) ? "total_volume" : "used_volume"}
              showAxes
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Team & Topics</CardTitle>
            <Button onClick={() => dispatch(openModal("team"))}>Edit</Button>
          </CardHeader>
          <CardContent>
            {teamRoleRows.length ? (
              <div className="grid gap-2">
                {teamRoleRows.map((row) => (
                  <div key={row.roleId} className="rounded-md border border-border bg-white p-2 text-sm">
                    <strong>{row.roleName}:</strong> {row.assignees.length ? row.assignees.join(", ") : "Unassigned"}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No role catalog available.</p>
            )}
          </CardContent>
        </Card>

        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>Derivatives</CardTitle>
          </CardHeader>
          <CardContent>
            <DerivativesSplitPanel
              derivatives={derivativeItems}
              selectedDerivativeId={selectedDerivative ? String(selectedDerivative.id) : null}
              onSelectDerivative={(id) => dispatch(setSelectedDerivativeId(id))}
              onEditDerivative={(id) => {
                dispatch(setSelectedDerivativeId(id));
                dispatch(openModal("derivative"));
              }}
              onEditVolumes={(id) => {
                dispatch(setSelectedDerivativeId(id));
                dispatch(setVolumeModalDerivativeId(id));
                dispatch(setSelectedVolumeId(null));
                dispatch(openModal("volume"));
              }}
            />
          </CardContent>
        </Card>
      </section>

      <Dialog open={modals.project} onClose={() => dispatch(closeModal("project"))} title="Edit Project">
        <div className="grid gap-2 md:grid-cols-2">
          <label className="text-sm">Project Name<input className="mt-1 w-full rounded border border-border p-2" value={projectForm.name} onChange={(event) => setProjectForm((prev) => ({ ...prev, name: event.target.value }))} /></label>
          <label className="text-sm">Customer<select className="mt-1 w-full rounded border border-border p-2" value={projectForm.customer} onChange={(event) => setProjectForm((prev) => ({ ...prev, customer: event.target.value }))}>{entities.customers.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.companyName} ({item.groupName})</option>)}</select></label>
          <label className="text-sm">Project Phase Type<select className="mt-1 w-full rounded border border-border p-2" value={projectForm.projectPhaseType} onChange={(event) => setProjectForm((prev) => ({ ...prev, projectPhaseType: event.target.value }))}>{entities.phaseTypes.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name}</option>)}</select></label>
          <label className="text-sm">Project Type<select className="mt-1 w-full rounded border border-border p-2" value={projectForm.projectType} onChange={(event) => setProjectForm((prev) => ({ ...prev, projectType: event.target.value }))}><option value="">--</option>{entities.projectTypes.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name}</option>)}</select></label>
          <label className="text-sm">Currency<select className="mt-1 w-full rounded border border-border p-2" value={projectForm.currency} onChange={(event) => setProjectForm((prev) => ({ ...prev, currency: event.target.value }))}>{entities.currencies.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name} ({item.abbreviation})</option>)}</select></label>
          <label className="text-sm">Probability of Nomination (%)<input className="mt-1 w-full rounded border border-border p-2" type="number" min="0" max="100" step="1" value={projectForm.probabilityOfNomination} onChange={(event) => setProjectForm((prev) => ({ ...prev, probabilityOfNomination: event.target.value }))} /></label>
        </div>
        <div className="mt-3"><Button variant="default" onClick={() => void onUpdateProject()}>Update Project</Button></div>
      </Dialog>

      <Dialog open={modals.customer} onClose={() => dispatch(closeModal("customer"))} title="Edit Customer">
        <div className="grid gap-2 md:grid-cols-2">
          <label className="text-sm">Company Name<input className="mt-1 w-full rounded border border-border p-2" value={customerForm.companyName} onChange={(event) => setCustomerForm((prev) => ({ ...prev, companyName: event.target.value }))} /></label>
          <label className="text-sm">Group Name<input className="mt-1 w-full rounded border border-border p-2" value={customerForm.groupName} onChange={(event) => setCustomerForm((prev) => ({ ...prev, groupName: event.target.value }))} /></label>
          <label className="text-sm">Customer Number<input className="mt-1 w-full rounded border border-border p-2" type="number" value={customerForm.number} onChange={(event) => setCustomerForm((prev) => ({ ...prev, number: event.target.value }))} /></label>
          <label className="text-sm">Key Account<select className="mt-1 w-full rounded border border-border p-2" value={customerForm.keyAccount} onChange={(event) => setCustomerForm((prev) => ({ ...prev, keyAccount: event.target.value }))}><option value="">--</option>{entities.users.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.fullName || item.username}</option>)}</select></label>
        </div>
        <div className="mt-3"><Button variant="default" onClick={() => void onUpdateCustomer()}>Update Customer</Button></div>
      </Dialog>

      <Dialog open={modals.derivative} onClose={() => dispatch(closeModal("derivative"))} title="Edit Derivative">
        <div className="grid gap-2 md:grid-cols-2">
          <label className="text-sm">Select Derivative<select className="mt-1 w-full rounded border border-border p-2" value={String(selectedDerivative?.id || "")} onChange={(event) => dispatch(setSelectedDerivativeId(event.target.value))}>{derivativeItems.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name} (#{item.id})</option>)}</select></label>
          <label className="text-sm">Derivative Name<input className="mt-1 w-full rounded border border-border p-2" value={derivativeForm.name} onChange={(event) => setDerivativeForm((prev) => ({ ...prev, name: event.target.value }))} /></label>
          <label className="text-sm">Derivative Type<select className="mt-1 w-full rounded border border-border p-2" value={derivativeForm.derivativeType} onChange={(event) => setDerivativeForm((prev) => ({ ...prev, derivativeType: event.target.value }))}>{entities.derivativeTypes.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name}</option>)}</select></label>
          <label className="text-sm">Plant<select className="mt-1 w-full rounded border border-border p-2" value={derivativeForm.plant} onChange={(event) => setDerivativeForm((prev) => ({ ...prev, plant: event.target.value }))}>{entities.plants.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name}</option>)}</select></label>
          <label className="text-sm">Pieces per Car Set<input className="mt-1 w-full rounded border border-border p-2" type="number" min="1" value={derivativeForm.pieces} onChange={(event) => setDerivativeForm((prev) => ({ ...prev, pieces: event.target.value }))} /></label>
          <label className="text-sm">Norm Daily Quantity<input className="mt-1 w-full rounded border border-border p-2" type="number" min="0" value={derivativeForm.norm} onChange={(event) => setDerivativeForm((prev) => ({ ...prev, norm: event.target.value }))} /></label>
          <label className="text-sm md:col-span-2">Volume Description<textarea className="mt-1 w-full rounded border border-border p-2" value={derivativeForm.description} onChange={(event) => setDerivativeForm((prev) => ({ ...prev, description: event.target.value }))} /></label>
        </div>
        <div className="mt-3"><Button variant="default" onClick={() => void onUpdateDerivative()}>Update Derivative</Button></div>
      </Dialog>

      <Dialog open={modals.volume} onClose={() => dispatch(closeModal("volume"))} title="Edit Volume and Curve Points">
        <div className="grid gap-2 md:grid-cols-2">
          <label className="text-sm">Select Volume<select className="mt-1 w-full rounded border border-border p-2" value={String(selectedVolume?.id || "")} onChange={(event) => dispatch(setSelectedVolumeId(event.target.value))}>{volumeItems.map((item) => <option key={String(item.id)} value={String(item.id)}>#{item.id} {item.derivativeName}</option>)}</select></label>
          <label className="text-sm">Derivative<select className="mt-1 w-full rounded border border-border p-2" value={volumeForm.derivative} onChange={(event) => setVolumeForm((prev) => ({ ...prev, derivative: event.target.value }))}>{derivativeItems.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name} (#{item.id})</option>)}</select></label>
          <label className="text-sm">Project Phase Type<select className="mt-1 w-full rounded border border-border p-2" value={volumeForm.phaseType} onChange={(event) => setVolumeForm((prev) => ({ ...prev, phaseType: event.target.value }))}><option value="">--</option>{entities.phaseTypes.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name}</option>)}</select></label>
          <label className="text-sm">SOP<input className="mt-1 w-full rounded border border-border p-2" type="date" value={volumeForm.sop} onChange={(event) => setVolumeForm((prev) => ({ ...prev, sop: event.target.value }))} /></label>
          <label className="text-sm">EOP<input className="mt-1 w-full rounded border border-border p-2" type="date" value={volumeForm.eop} onChange={(event) => setVolumeForm((prev) => ({ ...prev, eop: event.target.value }))} /></label>
          <label className="flex items-center gap-2 rounded border border-border bg-muted/30 p-2 text-sm"><input type="checkbox" checked={volumeForm.used} onChange={(event) => setVolumeForm((prev) => ({ ...prev, used: event.target.checked }))} /> usedVolume</label>
          <label className="flex items-center gap-2 rounded border border-border bg-muted/30 p-2 text-sm"><input type="checkbox" checked={volumeForm.vehicles} onChange={(event) => setVolumeForm((prev) => ({ ...prev, vehicles: event.target.checked }))} /> isVolumeInVehicles</label>
          <label className="text-sm md:col-span-2">Description<textarea className="mt-1 w-full rounded border border-border p-2" value={volumeForm.description} onChange={(event) => setVolumeForm((prev) => ({ ...prev, description: event.target.value }))} /></label>
        </div>

        <div className="mt-3"><Button variant="default" onClick={() => void onUpdateVolume()}>Update Volume</Button></div>

        <div className="mt-4">
          <h4 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Volume Curve Preview</h4>
          <PrimaryCurve data={modalCurve} dataKey="value" />
        </div>

        <div className="mt-4">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Button onClick={() => setCurveRows((prev) => [...prev, { id: null, volumeDate: "", volume: 0 }])}>Add Row</Button>
            <Button variant="default" onClick={() => void onSaveAllRows()}>Save All Rows</Button>
            <Button onClick={() => setCurveRows((selectedVolume?.curvePoints || []).map((item) => ({ id: String(item.id), volumeDate: item.volumeDate, volume: Number(item.volume || 0) })))}>Reload Rows</Button>
          </div>
          <div className="overflow-auto rounded-md border border-border">
            <Table className="min-w-[560px]">
              <THead>
                <tr>
                  <TH>Year Start (Jan 1)</TH>
                  <TH>Volume</TH>
                  <TH>Actions</TH>
                </tr>
              </THead>
              <TBody>
                {curveRows.length ? curveRows.map((row, idx) => (
                  <tr key={`${row.id || "new"}-${idx}`}>
                    <TD>
                      <input
                        className="w-full rounded border border-border p-2"
                        type="date"
                        value={row.volumeDate}
                        onChange={(event) =>
                          setCurveRows((prev) =>
                            prev.map((item, i) =>
                              i === idx ? { ...item, volumeDate: yearStartDate(event.target.value) || event.target.value } : item
                            )
                          )
                        }
                      />
                    </TD>
                    <TD><input className="w-full rounded border border-border p-2" type="number" min="0" step="1" value={row.volume} onChange={(event) => setCurveRows((prev) => prev.map((item, i) => (i === idx ? { ...item, volume: Number(event.target.value || 0) } : item)))} /></TD>
                    <TD className="flex gap-2">
                      <Button onClick={() => void onSaveCurveRow(row)}>Save</Button>
                      <Button variant="destructive" onClick={() => void onDeleteCurveRow(row)}>Delete</Button>
                    </TD>
                  </tr>
                )) : (
                  <tr><TD className="text-muted-foreground" colSpan={3}>No curve points. Add your first row.</TD></tr>
                )}
              </TBody>
            </Table>
          </div>
        </div>
      </Dialog>

      <Dialog open={modals.team} onClose={() => dispatch(closeModal("team"))} title="Edit Project Team">
        <div className="grid gap-2">
          {teamRoleRows.map((row) => (
            <label key={row.roleId} className="grid gap-1 rounded-md border border-border bg-white p-2 text-sm md:grid-cols-[220px_1fr] md:items-center">
              <span className="font-medium">{row.roleName}</span>
              <select
                className="w-full rounded border border-border p-2"
                value={teamAssignments[row.roleId] ?? ""}
                onChange={(event) =>
                  setTeamAssignments((prev) => ({
                    ...prev,
                    [row.roleId]: event.target.value,
                  }))
                }
              >
                <option value="">Unassigned</option>
                {entities.users.map((user) => (
                  <option key={String(user.id)} value={String(user.id)}>
                    {user.fullName || user.username || `User #${user.id}`}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="default" disabled={teamSaving} onClick={() => void onSaveTeamAssignments()}>
            {teamSaving ? "Saving..." : "Save Team Assignments"}
          </Button>
          <Button onClick={() => dispatch(closeModal("team"))}>Cancel</Button>
        </div>
      </Dialog>
    </main>
  );
}
