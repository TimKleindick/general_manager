import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
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
  updateProject: `mutation UpdateProject($id: Int!, $name: String!, $customer: ID!, $projectPhaseType: ID!, $projectType: ID, $currency: ID!, $probabilityOfNomination: MeasurementScalar, $customerVolumeFlex: MeasurementScalar) { updateProject(id: $id, name: $name, customer: $customer, projectPhaseType: $projectPhaseType, projectType: $projectType, currency: $currency, probabilityOfNomination: $probabilityOfNomination, customerVolumeFlex: $customerVolumeFlex) { success } }`,
  updateCustomer: `mutation UpdateCustomer($id: Int!, $companyName: String!, $groupName: String!, $number: Int, $keyAccount: ID) { updateCustomer(id: $id, companyName: $companyName, groupName: $groupName, number: $number, keyAccount: $keyAccount) { success } }`,
  updateDerivative: `mutation UpdateDerivative($id: Int!, $project: ID!, $name: String!, $derivativeType: ID!, $Plant: ID!, $piecesPerCarSet: Int, $normDailyQuantity: Int, $maxDailyQuantity: Int, $volumeDescription: String) { updateDerivative(id: $id, project: $project, name: $name, derivativeType: $derivativeType, Plant: $Plant, piecesPerCarSet: $piecesPerCarSet, normDailyQuantity: $normDailyQuantity, maxDailyQuantity: $maxDailyQuantity, volumeDescription: $volumeDescription) { success } }`,
  createVolume: `mutation CreateVolume($derivative: ID!, $sop: Date!, $eop: Date!) { createCustomerVolume(derivative: $derivative, sop: $sop, eop: $eop) { success CustomerVolume { id } } }`,
  updateVolume: `mutation UpdateVolume($id: Int!, $derivative: ID!, $projectPhaseType: ID, $sop: Date!, $eop: Date!, $description: String, $usedVolume: Boolean, $isVolumeInVehicles: Boolean) { updateCustomerVolume(id: $id, derivative: $derivative, projectPhaseType: $projectPhaseType, sop: $sop, eop: $eop, description: $description, usedVolume: $usedVolume, isVolumeInVehicles: $isVolumeInVehicles) { success } }`,
  replaceCurvePoints: `mutation ReplaceCurvePoints($customerVolume: ID!, $volumeDates: [Date]!, $volumes: [Int]!) { replaceCustomerVolumeCurvePoints(customerVolume: $customerVolume, volumeDates: $volumeDates, volumes: $volumes) { success } }`,
  createProjectTeam: `mutation CreateProjectTeam($project: ID!, $projectUserRole: ID!, $responsibleUser: ID!, $active: Boolean) { createProjectTeam(project: $project, projectUserRole: $projectUserRole, responsibleUser: $responsibleUser, active: $active) { success } }`,
  updateProjectTeam: `mutation UpdateProjectTeam($id: Int!, $project: ID!, $projectUserRole: ID!, $responsibleUser: ID!, $active: Boolean) { updateProjectTeam(id: $id, project: $project, projectUserRole: $projectUserRole, responsibleUser: $responsibleUser, active: $active) { success } }`,
  deleteProjectTeam: `mutation DeleteProjectTeam($id: Int!) { deleteProjectTeam(id: $id) { success } }`,
};
const WORKFLOW_TASK_LANES = ["Todo", "In Progress", "Blocked", "Done"] as const;
type WorkflowTaskLane = (typeof WORKFLOW_TASK_LANES)[number];

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

function yearDatesFromRange(sop: string, eop: string) {
  const sopYear = Number((sop || "").slice(0, 4));
  const eopYear = Number((eop || "").slice(0, 4));
  if (!Number.isFinite(sopYear) || !Number.isFinite(eopYear)) return [] as string[];
  if (eopYear < sopYear) return [] as string[];
  const dates: string[] = [];
  for (let year = sopYear; year <= eopYear; year += 1) {
    dates.push(`${year}-01-01`);
  }
  return dates;
}

function requireValue(value: string, msg: string) {
  if (String(value || "").trim()) return null;
  return msg;
}

function getCookie(name: string): string | null {
  const value = document.cookie
    .split("; ")
    .find((row) => row.startsWith(`${name}=`))
    ?.split("=")[1];
  return value ? decodeURIComponent(value) : null;
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
    customerVolumeFlex: "",
  });
  const [customerForm, setCustomerForm] = useState({ companyName: "", groupName: "", number: "", keyAccount: "" });
  const [derivativeForm, setDerivativeForm] = useState({ name: "", project: "", derivativeType: "", plant: "", pieces: "", norm: "", max: "", description: "" });
  const [volumeForm, setVolumeForm] = useState({ derivative: "", phaseType: "", sop: "", eop: "", description: "", used: false, vehicles: false });
  const [curveRows, setCurveRows] = useState<EditableCurvePoint[]>([]);
  const [isCreatingVolume, setIsCreatingVolume] = useState(false);
  const [volumeSaving, setVolumeSaving] = useState(false);
  const [teamAssignments, setTeamAssignments] = useState<Record<string, string>>({});
  const [teamSaving, setTeamSaving] = useState(false);
  const [projectImageUploading, setProjectImageUploading] = useState(false);
  const [pendingProjectImageFile, setPendingProjectImageFile] = useState<File | null>(null);
  const [pendingProjectImagePreview, setPendingProjectImagePreview] = useState<string | null>(null);
  const projectImageInputRef = useRef<HTMLInputElement | null>(null);
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

  const selectedVolume = useMemo(() => {
    if (isCreatingVolume) return null;
    if (selectedVolumeId) {
      return volumeItems.find((item) => String(item.id) === String(selectedVolumeId)) || null;
    }
    return volumeItems.find((item) => Boolean(item.usedVolume)) || volumeItems[0] || null;
  }, [volumeItems, selectedVolumeId, isCreatingVolume]);
  const fixedVolumeDerivativeId = useMemo(
    () => String(volumeModalDerivativeId || selectedDerivative?.id || derivativeItems[0]?.id || ""),
    [volumeModalDerivativeId, selectedDerivative?.id, derivativeItems]
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
      customerVolumeFlex: String(percentValue(project.customerVolumeFlex) ?? ""),
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
      max: String(selectedDerivative.maxDailyQuantity ?? ""),
      description: selectedDerivative.volumeDescription || "",
    });
  }, [selectedDerivative, project?.id]);

  useEffect(() => {
    if (!selectedVolume) {
      if (modals.volume && volumeItems.length === 0 && !isCreatingVolume) {
        const preferredDerivativeId = String(
          volumeModalDerivativeId || selectedDerivative?.id || derivativeItems[0]?.id || ""
        );
        setIsCreatingVolume(true);
        setCurveRows([]);
        setVolumeForm({
          derivative: preferredDerivativeId,
          phaseType: "",
          sop: "",
          eop: "",
          description: "",
          used: false,
          vehicles: false,
        });
      }
      return;
    }
    setIsCreatingVolume(false);
    setVolumeForm({
      derivative: String(selectedVolume.derivativeId || ""),
      phaseType: String(selectedVolume.projectPhaseType?.id || ""),
      sop: selectedVolume.sop || "",
      eop: selectedVolume.eop || "",
      description: selectedVolume.description || "",
      used: Boolean(selectedVolume.usedVolume),
      vehicles: Boolean(selectedVolume.isVolumeInVehicles),
    });
    const selectedPointsByDate = new Map(
      (selectedVolume.curvePoints || []).map((point) => [yearStartDate(point.volumeDate) || point.volumeDate, { id: String(point.id), volume: Number(point.volume || 0) }])
    );
    const dates = yearDatesFromRange(selectedVolume.sop || "", selectedVolume.eop || "");
    setCurveRows(
      dates.map((date) => {
        const existing = selectedPointsByDate.get(date);
        return {
          id: existing?.id ?? null,
          volumeDate: date,
          volume: existing?.volume ?? 0,
        };
      })
    );
  }, [
    selectedVolume,
    modals.volume,
    volumeItems.length,
    isCreatingVolume,
    volumeModalDerivativeId,
    selectedDerivative?.id,
    derivativeItems,
  ]);

  useEffect(() => {
    if (!modals.volume) return;
    const dates = yearDatesFromRange(volumeForm.sop, volumeForm.eop);
    setCurveRows((prev) => {
      const prevByDate = new Map(prev.map((item) => [item.volumeDate, item]));
      return dates.map((date) => {
        const existing = prevByDate.get(date);
        return {
          id: existing?.id ?? null,
          volumeDate: date,
          volume: existing?.volume ?? 0,
        };
      });
    });
  }, [modals.volume, volumeForm.sop, volumeForm.eop]);

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

  const notifyError = (entityType: string, entityId: string, message: string) => {
    dispatch(
      pushNotification({
        event: "error",
        entityType,
        entityId,
        message,
      })
    );
  };

  const onUpdateProject = async () => {
    const missing =
      requireValue(projectForm.name, "Project name is required") ||
      requireValue(projectForm.customer, "Project customer is required") ||
      requireValue(projectForm.projectPhaseType, "Project phase type is required") ||
      requireValue(projectForm.currency, "Project currency is required");
    if (missing || !projectId) {
      notifyError("Project", String(projectId || "-"), missing || "Project update failed");
      return;
    }
    try {
      await dispatch(mutate({
        mutation: MUTATIONS.updateProject,
        variables: {
        id: Number(projectId),
        name: projectForm.name,
        customer: projectForm.customer,
        projectPhaseType: projectForm.projectPhaseType,
        projectType: projectForm.projectType || null,
        currency: projectForm.currency,
        probabilityOfNomination: projectForm.probabilityOfNomination
          ? `${Number(projectForm.probabilityOfNomination)} percent`
          : null,
        customerVolumeFlex: projectForm.customerVolumeFlex
          ? `${Number(projectForm.customerVolumeFlex)} percent`
          : null,
      },
      })).unwrap();

      if (pendingProjectImageFile) {
        setProjectImageUploading(true);
        try {
          const csrfToken = getCookie("csrftoken");
          const formData = new FormData();
          formData.append("image", pendingProjectImageFile);
          const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/image/`, {
            method: "POST",
            credentials: "same-origin",
            headers: csrfToken ? { "X-CSRFToken": csrfToken } : undefined,
            body: formData,
          });
          const payload = (await response.json()) as { ok?: boolean; error?: string };
          if (!response.ok || !payload.ok) {
            throw new Error(payload.error || "Could not upload project image.");
          }
        } finally {
          setProjectImageUploading(false);
        }
      }

      dispatch(pushNotification({ event: "updated", entityType: "Project", entityId: String(projectId), message: "Project data saved" }));
      await dispatch(fetchDashboardProject(projectId)).unwrap();
      setPendingProjectImageFile(null);
      if (pendingProjectImagePreview) {
        URL.revokeObjectURL(pendingProjectImagePreview);
        setPendingProjectImagePreview(null);
      }
      if (projectImageInputRef.current) {
        projectImageInputRef.current.value = "";
      }
      dispatch(closeModal("project"));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Project update failed";
      dispatch(
        pushNotification({
          event: "error",
          entityType: "Project",
          entityId: String(projectId),
          message,
        })
      );
    }
  };

  const onUpdateCustomer = async () => {
    const missing = requireValue(customerForm.companyName, "Company name is required") || requireValue(customerForm.groupName, "Group name is required");
    if (missing || !project?.customer?.id) {
      notifyError("Customer", String(project?.customer?.id || "-"), missing || "Customer update failed");
      return;
    }
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
    if (missing) {
      notifyError("Derivative", String(selectedDerivative.id), missing);
      return;
    }
    await runMutationAndRefresh(MUTATIONS.updateDerivative, {
      id: Number(selectedDerivative.id),
      project: String(projectId),
      name: derivativeForm.name,
      derivativeType: derivativeForm.derivativeType,
      Plant: derivativeForm.plant,
      piecesPerCarSet: Number(derivativeForm.pieces || 1),
      normDailyQuantity: Number(derivativeForm.norm || 0),
      maxDailyQuantity: derivativeForm.max ? Number(derivativeForm.max) : null,
      volumeDescription: derivativeForm.description || null,
    }, "updated", "Derivative", String(selectedDerivative.id), "Derivative data saved");
    dispatch(closeModal("derivative"));
  };

  const startCreateVolumeDraft = () => {
    const preferredDerivativeId = fixedVolumeDerivativeId;
    setIsCreatingVolume(true);
    dispatch(setSelectedVolumeId(null));
    setCurveRows([]);
    setVolumeForm({
      derivative: preferredDerivativeId,
      phaseType: "",
      sop: "",
      eop: "",
      description: "",
      used: false,
      vehicles: false,
    });
  };

  useEffect(() => {
    if (!modals.volume) {
      setIsCreatingVolume(false);
    }
  }, [modals.volume]);

  const onSaveVolumeEditor = async () => {
    if (volumeSaving) return;
    if (!projectId) return;
    if (!fixedVolumeDerivativeId) {
      notifyError("Volume", "-", "No derivative selected for this editor.");
      return;
    }
    const missing =
      requireValue(volumeForm.sop, "Volume SOP is required") ||
      requireValue(volumeForm.eop, "Volume EOP is required");
    if (missing) {
      notifyError("Volume", String(selectedVolume?.id || "-"), missing);
      return;
    }

    setVolumeSaving(true);
    try {
      let targetVolumeId = selectedVolume ? String(selectedVolume.id) : null;

      if (!targetVolumeId) {
        const createPayload = await dispatch(
          mutate({
            mutation: MUTATIONS.createVolume,
            variables: {
              derivative: fixedVolumeDerivativeId,
              sop: volumeForm.sop,
              eop: volumeForm.eop,
            },
          })
        ).unwrap();
        const createResult = (
          createPayload as {
            createCustomerVolume?: {
              success?: boolean;
              CustomerVolume?: { id?: string | number | null } | null;
            };
          }
        ).createCustomerVolume;
        if (!createResult?.success || createResult?.CustomerVolume?.id == null) {
          throw new Error("Create volume mutation returned success=false");
        }
        targetVolumeId = String(createResult.CustomerVolume.id);
      }

      await dispatch(
        mutate({
          mutation: MUTATIONS.updateVolume,
          variables: {
            id: Number(targetVolumeId),
            derivative: fixedVolumeDerivativeId,
            projectPhaseType: volumeForm.phaseType || null,
            sop: volumeForm.sop,
            eop: volumeForm.eop,
            description: volumeForm.description || null,
            usedVolume: volumeForm.used,
            isVolumeInVehicles: volumeForm.vehicles,
          },
        })
      ).unwrap();

      await dispatch(
        mutate({
          mutation: MUTATIONS.replaceCurvePoints,
          variables: {
            customerVolume: String(targetVolumeId),
            volumeDates: curveRows.map((row) => row.volumeDate),
            volumes: curveRows.map((row) => Number(row.volume || 0)),
          },
        })
      ).unwrap();

      dispatch(
        pushNotification({
          event: "saved",
          entityType: "Volume",
          entityId: String(targetVolumeId),
          message: "Volume and yearly figures saved",
        })
      );
      await dispatch(fetchDashboardProject(projectId)).unwrap();
      dispatch(setSelectedVolumeId(String(targetVolumeId)));
      setIsCreatingVolume(false);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not save volume data";
      notifyError("Volume", String(selectedVolume?.id || "-"), message);
    } finally {
      setVolumeSaving(false);
    }
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

  const workflowTasks = useMemo(() => {
    const assignedRoles = teamRoleRows.filter((row) => row.assignees.length).length;
    const requiredRoles = teamRoleRows.length;
    const nomination = percentValue(project?.probabilityOfNomination);
    const tasks: Array<{ id: string; title: string; detail: string; lane: WorkflowTaskLane }> = [
      {
        id: "team-assignment",
        title: "Complete team role assignments",
        detail: `${assignedRoles}/${requiredRoles || 1} roles assigned`,
        lane: requiredRoles > 0 && assignedRoles >= requiredRoles ? "Done" : assignedRoles > 0 ? "In Progress" : "Todo",
      },
      {
        id: "key-account-confirm",
        title: "Confirm key account mapping",
        detail: project?.customer?.keyAccount?.fullName || project?.customer?.keyAccount?.username || "No key account assigned",
        lane: project?.customer?.keyAccount ? "Done" : "Blocked",
      },
      {
        id: "curve-validation",
        title: "Validate volume curve rows",
        detail: `${projectTotals.volumeCount} volume entries, ${projectTotals.derivativeCount} derivatives`,
        lane: projectTotals.volumeCount > 0 ? "In Progress" : "Todo",
      },
      {
        id: "nomination-review",
        title: "Review nomination confidence",
        detail: nomination == null ? "Nomination is not set" : `Current confidence ${Math.round(nomination)}%`,
        lane: nomination == null ? "Todo" : nomination < 40 ? "Blocked" : nomination >= 70 ? "Done" : "In Progress",
      },
    ];
    return tasks;
  }, [project?.customer?.keyAccount, project?.probabilityOfNomination, projectTotals.derivativeCount, projectTotals.volumeCount, teamRoleRows]);

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
      notifyError("ProjectTeam", String(projectId), `Could not save team changes: ${message}`);
    } finally {
      setTeamSaving(false);
    }
  };

  const onProjectImageSelected = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (pendingProjectImagePreview) {
      URL.revokeObjectURL(pendingProjectImagePreview);
    }
    setPendingProjectImageFile(file);
    setPendingProjectImagePreview(URL.createObjectURL(file));
  };

  useEffect(() => {
    return () => {
      if (pendingProjectImagePreview) {
        URL.revokeObjectURL(pendingProjectImagePreview);
      }
    };
  }, [pendingProjectImagePreview]);

  return (
    <main className="mx-auto max-w-[1460px] p-4 pb-16">
      <div className="mb-4 h-2 rounded-full bg-gradient-to-r from-teal-700 via-teal-500 to-sky-500" />

      <header className="mb-3 rounded-xl border border-border bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="font-serif text-4xl text-slate-900">/dashboard</h1>
            <p className="mt-1 text-sm text-slate-600">
              #{project?.id || projectId} {project?.name || "Loading project..."}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              Project team, volume workflow, and key-account context in one view
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => navigate("/projects/?restore=1&view=table")}>Back to Projects</Button>
            <Badge>WS: {wsStatus}</Badge>
            <Badge>Customer: {project?.customer?.companyName || "-"}</Badge>
          </div>
        </div>
      </header>

      <section className="grid gap-3 lg:grid-cols-[1.9fr_1fr]">
        <Card className="border-border bg-white shadow-sm">
          <CardHeader>
            <CardTitle>Project Overview</CardTitle>
            <Button onClick={() => dispatch(openModal("project"))}>Edit Details</Button>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3">
              <div className="order-1 grid gap-2 text-sm md:grid-cols-2">
                <p><strong>Phase:</strong> {project?.projectPhaseType?.name || "-"}</p>
                <p><strong>Type:</strong> {project?.projectType?.name || "-"}</p>
                <p><strong>Currency:</strong> {project?.currency?.abbreviation || project?.currency?.name || "-"}</p>
                <p><strong>Nomination:</strong> {projectTotals.nomination}</p>
                <p><strong>Total Volume:</strong> {Number(project?.totalVolume || 0).toLocaleString()} pcs</p>
                <p><strong>Volume Flex:</strong> {formatPercent(project?.customerVolumeFlex)}</p>
              </div>
              <div className="order-2 rounded-md border border-border bg-slate-50 p-2">
                {project?.projectImageUrl ? (
                  <img
                    className="h-[28rem] w-full rounded object-cover"
                    src={project.projectImageUrl}
                    alt="Project overview"
                  />
                ) : (
                  <div className="flex h-[28rem] w-full items-center justify-center rounded border border-dashed border-border bg-white text-xs text-slate-500">
                    No project image
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        <div className="grid gap-3">
          <Card className="border-border bg-white shadow-sm">
            <CardHeader>
              <CardTitle>Customer</CardTitle>
              <Button onClick={() => dispatch(openModal("customer"))}>Edit Customer</Button>
            </CardHeader>
            <CardContent>
              <div className="grid gap-2 text-sm">
                <p><strong>Company:</strong> {project?.customer?.companyName || "-"}</p>
                <p><strong>Group:</strong> {project?.customer?.groupName || "-"}</p>
                <p><strong>Number:</strong> {project?.customer?.number ?? "-"}</p>
                <p><strong>Key Account:</strong> {project?.customer?.keyAccount?.fullName || project?.customer?.keyAccount?.username || "-"}</p>
              </div>
            </CardContent>
          </Card>

          <Card className="border-border bg-white shadow-sm">
            <CardHeader>
              <CardTitle>Volume Summary</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="mb-3 grid gap-2 text-sm">
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
        </div>
      </section>

      <section className="mt-3 grid gap-3 lg:grid-cols-[340px_1fr]">
        <Card className="border-border bg-white shadow-sm">
          <CardHeader>
            <CardTitle>Team & Topics</CardTitle>
            <Button onClick={() => dispatch(openModal("team"))}>Edit</Button>
          </CardHeader>
          <CardContent>
            {teamRoleRows.length ? (
              <div className="grid gap-2">
                {teamRoleRows.map((row) => (
                  <div key={row.roleId} className="rounded-md border border-border bg-slate-50 p-2 text-sm">
                    <strong>{row.roleName}:</strong> {row.assignees.length ? row.assignees.join(", ") : "Unassigned"}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No role catalog available.</p>
            )}
          </CardContent>
        </Card>

        <Card className="border-border bg-white shadow-sm">
          <CardHeader>
            <CardTitle>Derivatives + Volume Editor</CardTitle>
            <div className="flex gap-2">
              <Button
                variant="default"
                onClick={() => {
                  if (selectedDerivative) {
                    dispatch(setSelectedDerivativeId(String(selectedDerivative.id)));
                    dispatch(setVolumeModalDerivativeId(String(selectedDerivative.id)));
                    dispatch(setSelectedVolumeId(null));
                  }
                  dispatch(openModal("volume"));
                }}
              >
                Open Volume Table
              </Button>
            </div>
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

      <section className="mt-3 rounded-xl border border-border bg-white p-4 shadow-sm">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-serif text-2xl text-slate-900">Workflow Snapshot</h2>
          <p className="text-xs text-slate-500">Read-only task board (backend task manager can be connected later)</p>
        </div>
        <div className="grid gap-3 xl:grid-cols-4">
          {WORKFLOW_TASK_LANES.map((lane) => {
            const laneTasks = workflowTasks.filter((task) => task.lane === lane);
            return (
              <div
                key={lane}
                className="rounded-lg border border-border bg-slate-50/70 p-3"
              >
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-slate-700">{lane}</h3>
                  <Badge>{laneTasks.length}</Badge>
                </div>
                <div className="grid gap-2">
                  {laneTasks.length ? (
                    laneTasks.map((task) => (
                      <div key={task.id} className="rounded-md border border-border bg-white p-2 text-sm">
                        <p className="font-medium text-slate-900">{task.title}</p>
                        <p className="mt-1 text-xs text-slate-500">{task.detail}</p>
                      </div>
                    ))
                  ) : (
                    <p className="rounded-md border border-dashed border-border bg-white px-2 py-4 text-center text-xs text-slate-500">
                      No tasks in this lane
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <Dialog open={modals.project} onClose={() => dispatch(closeModal("project"))} title="Edit Project">
        <div className="grid gap-2 md:grid-cols-2">
          <label className="text-sm">Project Name<input className="mt-1 w-full rounded border border-border p-2" value={projectForm.name} onChange={(event) => setProjectForm((prev) => ({ ...prev, name: event.target.value }))} /></label>
          <label className="text-sm">Customer<select className="mt-1 w-full rounded border border-border p-2" value={projectForm.customer} onChange={(event) => setProjectForm((prev) => ({ ...prev, customer: event.target.value }))}>{entities.customers.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.companyName} ({item.groupName})</option>)}</select></label>
          <label className="text-sm">Project Phase Type<select className="mt-1 w-full rounded border border-border p-2" value={projectForm.projectPhaseType} onChange={(event) => setProjectForm((prev) => ({ ...prev, projectPhaseType: event.target.value }))}>{entities.phaseTypes.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name}</option>)}</select></label>
          <label className="text-sm">Project Type<select className="mt-1 w-full rounded border border-border p-2" value={projectForm.projectType} onChange={(event) => setProjectForm((prev) => ({ ...prev, projectType: event.target.value }))}><option value="">--</option>{entities.projectTypes.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name}</option>)}</select></label>
          <label className="text-sm">Currency<select className="mt-1 w-full rounded border border-border p-2" value={projectForm.currency} onChange={(event) => setProjectForm((prev) => ({ ...prev, currency: event.target.value }))}>{entities.currencies.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name} ({item.abbreviation})</option>)}</select></label>
          <label className="text-sm">Probability of Nomination (%)<input className="mt-1 w-full rounded border border-border p-2" type="number" min="0" max="100" step="1" value={projectForm.probabilityOfNomination} onChange={(event) => setProjectForm((prev) => ({ ...prev, probabilityOfNomination: event.target.value }))} /></label>
          <label className="text-sm">Volume Flex (%)<input className="mt-1 w-full rounded border border-border p-2" type="number" min="0" max="100" step="1" value={projectForm.customerVolumeFlex} onChange={(event) => setProjectForm((prev) => ({ ...prev, customerVolumeFlex: event.target.value }))} /></label>
          <div className="order-last rounded-md border border-border bg-slate-50 p-2 md:col-span-2">
            <p className="mb-2 text-sm font-medium">Project Image</p>
            {(pendingProjectImagePreview || project?.projectImageUrl) ? (
              <img
                className="h-52 w-full rounded object-cover"
                src={pendingProjectImagePreview || project?.projectImageUrl || ""}
                alt="Project overview"
              />
            ) : (
              <div className="flex h-52 w-full items-center justify-center rounded border border-dashed border-border bg-white text-xs text-slate-500">
                No project image
              </div>
            )}
            <input
              ref={projectImageInputRef}
              className="hidden"
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              onChange={(event) => void onProjectImageSelected(event)}
            />
            <div className="mt-2">
              {pendingProjectImageFile ? (
                <p className="mb-2 text-xs text-slate-600">
                  Selected image: {pendingProjectImageFile.name}. It will be saved when you click "Update Project".
                </p>
              ) : null}
              <Button
                onClick={() => projectImageInputRef.current?.click()}
                disabled={projectImageUploading || !projectId || !modals.project}
              >
                {projectImageUploading ? "Saving image..." : "Choose Image"}
              </Button>
            </div>
          </div>
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
          <label className="text-sm">Max Daily Quantity<input className="mt-1 w-full rounded border border-border p-2" type="number" min="0" value={derivativeForm.max} onChange={(event) => setDerivativeForm((prev) => ({ ...prev, max: event.target.value }))} /></label>
          <label className="text-sm md:col-span-2">Volume Description<textarea className="mt-1 w-full rounded border border-border p-2" value={derivativeForm.description} onChange={(event) => setDerivativeForm((prev) => ({ ...prev, description: event.target.value }))} /></label>
        </div>
        <div className="mt-3"><Button variant="default" onClick={() => void onUpdateDerivative()}>Update Derivative</Button></div>
      </Dialog>

      <Dialog
        open={modals.volume}
        onClose={() => {
          if (volumeSaving) return;
          dispatch(closeModal("volume"));
        }}
        title="Edit Volume and Curve Points"
        headerActions={
          <Button
            variant="default"
            onClick={() => void onSaveVolumeEditor()}
            disabled={!fixedVolumeDerivativeId || volumeSaving}
          >
            {volumeSaving ? "Saving..." : "Save All"}
          </Button>
        }
      >
        <div className="relative" aria-busy={volumeSaving}>
          {volumeSaving ? (
            <div className="absolute inset-0 z-20 flex items-center justify-center rounded-md bg-white/80 backdrop-blur-[1px]">
              <div className="flex items-center gap-2 rounded-md border border-border bg-white px-3 py-2 text-sm shadow-sm">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700" />
                Saving volume...
              </div>
            </div>
          ) : null}

        <div className="mb-3 flex items-center gap-2 overflow-auto">
          {volumeItems.map((item) => (
            <Button
              key={String(item.id)}
              variant={(!isCreatingVolume && String(selectedVolume?.id || "") === String(item.id)) ? "default" : "outline"}
              disabled={volumeSaving}
              onClick={() => {
                setIsCreatingVolume(false);
                dispatch(setSelectedVolumeId(String(item.id)));
              }}
            >
              Volume #{item.id}{item.usedVolume ? " [used]" : ""}
            </Button>
          ))}
          <Button
            variant={isCreatingVolume ? "default" : "outline"}
            disabled={volumeSaving}
            onClick={() => startCreateVolumeDraft()}
          >
            + New
          </Button>
        </div>

        <div className="grid gap-2 md:grid-cols-2">
          <label className="text-sm">Derivative
            <input
              className="mt-1 w-full rounded border border-border bg-muted/30 p-2"
              value={selectedDerivative?.name || derivativeItems.find((item) => String(item.id) === fixedVolumeDerivativeId)?.name || "-"}
              readOnly
            />
          </label>
          <label className="text-sm">Project Phase Type<select className="mt-1 w-full rounded border border-border p-2" value={volumeForm.phaseType} disabled={volumeSaving} onChange={(event) => setVolumeForm((prev) => ({ ...prev, phaseType: event.target.value }))}><option value="">--</option>{entities.phaseTypes.map((item) => <option key={String(item.id)} value={String(item.id)}>{item.name}</option>)}</select></label>
          <label className="text-sm">SOP<input className="mt-1 w-full rounded border border-border p-2" type="date" value={volumeForm.sop} disabled={volumeSaving} onChange={(event) => setVolumeForm((prev) => ({ ...prev, sop: event.target.value }))} /></label>
          <label className="text-sm">EOP<input className="mt-1 w-full rounded border border-border p-2" type="date" value={volumeForm.eop} disabled={volumeSaving} onChange={(event) => setVolumeForm((prev) => ({ ...prev, eop: event.target.value }))} /></label>
          <label className="flex items-center gap-2 rounded border border-border bg-muted/30 p-2 text-sm"><input type="checkbox" checked={volumeForm.used} disabled={volumeSaving} onChange={(event) => setVolumeForm((prev) => ({ ...prev, used: event.target.checked }))} /> usedVolume</label>
          <label className="flex items-center gap-2 rounded border border-border bg-muted/30 p-2 text-sm"><input type="checkbox" checked={volumeForm.vehicles} disabled={volumeSaving} onChange={(event) => setVolumeForm((prev) => ({ ...prev, vehicles: event.target.checked }))} /> isVolumeInVehicles</label>
          <label className="text-sm md:col-span-2">Description<textarea className="mt-1 w-full rounded border border-border p-2" value={volumeForm.description} disabled={volumeSaving} onChange={(event) => setVolumeForm((prev) => ({ ...prev, description: event.target.value }))} /></label>
        </div>

        <div className="mt-4">
          <h4 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Volume Curve Preview</h4>
          <PrimaryCurve data={modalCurve} dataKey="value" />
        </div>

        <div className="mt-4">
          <div className="overflow-auto rounded-md border border-border">
            <Table className="min-w-[520px] text-xs">
              <THead>
                <tr>
                  <TH className="w-20">Year</TH>
                  {curveRows.map((row) => (
                    <TH key={`year-${row.volumeDate}`} className="min-w-[92px] text-center">
                      {row.volumeDate.slice(0, 4)}
                    </TH>
                  ))}
                </tr>
              </THead>
              <TBody>
                {curveRows.length ? (
                  <tr>
                    <TD className="font-medium text-slate-700">Volume</TD>
                    {curveRows.map((row, idx) => (
                      <TD key={`vol-${row.volumeDate || `${row.id || "new"}-${idx}`}`}>
                      <input
                        className="h-8 w-full rounded border border-border px-2 py-1 text-center text-xs"
                        type="number"
                        min="0"
                        step="1"
                        disabled={volumeSaving}
                        value={row.volume}
                        onChange={(event) =>
                          setCurveRows((prev) =>
                            prev.map((item, i) => (i === idx ? { ...item, volume: Number(event.target.value || 0) } : item))
                          )
                        }
                      />
                      </TD>
                    ))}
                  </tr>
                ) : (
                  <tr><TD className="text-muted-foreground" colSpan={Math.max(2, curveRows.length + 1)}>{selectedVolume ? "No yearly rows. Set SOP/EOP and update the volume first." : "Create or select a volume to edit yearly figures."}</TD></tr>
                )}
              </TBody>
            </Table>
          </div>
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
