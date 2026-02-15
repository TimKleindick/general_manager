(function () {
  "use strict";

  const { executeQuery, executeMutation, createSubscriptionClient } = window.GMGraphQLClient;

  const params = new URLSearchParams(window.location.search);
  const projectId = params.get("projectId");
  if (!projectId) {
    window.location.replace("/projects/");
    return;
  }

  const state = {
    project: null,
    users: [],
    customers: [],
    phaseTypes: [],
    projectTypes: [],
    currencies: [],
    derivativeTypes: [],
    plants: [],
    selectedDerivativeId: null,
    selectedVolumeId: null,
    volumeModalDerivativeId: null,
    activeModal: null,
    lastFocused: null,
  };

  const refs = {
    wsStatus: document.getElementById("wsStatus"),
    projectHeadline: document.getElementById("projectHeadline"),
    formStatus: document.getElementById("formStatus"),
    projectIdChip: document.getElementById("projectIdChip"),
    customerChip: document.getElementById("customerChip"),
    overviewContent: document.getElementById("overviewContent"),
    customerContent: document.getElementById("customerContent"),
    volumeContent: document.getElementById("volumeContent"),
    volumeChart: document.getElementById("volumeChart"),
    derivativesContent: document.getElementById("derivativesContent"),
    teamContent: document.getElementById("teamContent"),
    activityLog: document.getElementById("activityLog"),
    volumeModalCurvePreview: document.getElementById("volumeModalCurvePreview"),
    curvePointTableBody: document.getElementById("curvePointTableBody"),
    backdrop: document.getElementById("modalBackdrop"),
    backToSelection: document.getElementById("backToSelection"),
    projectModal: document.getElementById("projectEditModal"),
    customerModal: document.getElementById("customerEditModal"),
    derivativeModal: document.getElementById("derivativeEditModal"),
    volumeModal: document.getElementById("volumeEditModal"),
  };

  const BOOTSTRAP_QUERY = `
    query DashboardBootstrapData {
      customerList(page: 1, pageSize: 300) {
        items { id companyName groupName number keyAccount { id fullName } }
      }
      userList(page: 1, pageSize: 300) {
        items { id fullName username }
      }
      projectphasetypeList(page: 1, pageSize: 100) { items { id name } }
      projecttypeList(page: 1, pageSize: 100) { items { id name } }
      currencyList(page: 1, pageSize: 100) { items { id name abbreviation } }
      derivativetypeList(page: 1, pageSize: 100) { items { id name } }
      plantList(page: 1, pageSize: 100) { items { id name } }
    }
  `;

  const DASHBOARD_QUERY = `
    query DashboardProject($id: ID!) {
      project(id: $id) {
        id
        name
        totalVolume
        probabilityOfNomination
        earliestSop
        latestEop
        customerVolumeFlex
        customer { id companyName groupName number keyAccount { id fullName username } }
        projectPhaseType { id name }
        projectType { id name }
        currency { id name abbreviation }
        projectteamList(pageSize: 200) {
          items {
            active
            projectUserRole { id name }
            responsibleUser { id fullName username }
          }
        }
        derivativeList(pageSize: 500) {
          items {
            id
            name
            derivativeType { id name }
            Plant { id name }
            piecesPerCarSet
            normDailyQuantity
            maxDailyQuantity
            volumeDescription
            customervolumeList(pageSize: 500) {
              items {
                id
                sop
                eop
                description
                usedVolume
                isVolumeInVehicles
                projectPhaseType { id name }
                customervolumecurvepointList(pageSize: 500) {
                  items {
                    id
                    volumeDate
                    volume
                  }
                }
              }
            }
          }
        }
      }
      projectvolumecurve(projectId: $id) {
        curveJson
      }
    }
  `;

  const PROJECT_UPDATE_MUTATION = `
    mutation UpdateProject($id: Int!, $name: String!, $customer: ID!, $projectPhaseType: ID!, $projectType: ID, $currency: ID!, $probabilityOfNomination: Float) {
      updateProject(id: $id, name: $name, customer: $customer, projectPhaseType: $projectPhaseType, projectType: $projectType, currency: $currency, probabilityOfNomination: $probabilityOfNomination) {
        success
        Project { id name }
      }
    }
  `;

  const CUSTOMER_UPDATE_MUTATION = `
    mutation UpdateCustomer($id: Int!, $companyName: String!, $groupName: String!, $number: Int, $keyAccount: ID) {
      updateCustomer(id: $id, companyName: $companyName, groupName: $groupName, number: $number, keyAccount: $keyAccount) {
        success
        Customer { id companyName groupName }
      }
    }
  `;

  const DERIVATIVE_UPDATE_MUTATION = `
    mutation UpdateDerivative($id: Int!, $project: ID!, $name: String!, $derivativeType: ID!, $Plant: ID!, $piecesPerCarSet: Int, $normDailyQuantity: Int, $volumeDescription: String) {
      updateDerivative(id: $id, project: $project, name: $name, derivativeType: $derivativeType, Plant: $Plant, piecesPerCarSet: $piecesPerCarSet, normDailyQuantity: $normDailyQuantity, volumeDescription: $volumeDescription) {
        success
        Derivative { id name }
      }
    }
  `;

  const VOLUME_UPDATE_MUTATION = `
    mutation UpdateVolume($id: Int!, $derivative: ID!, $projectPhaseType: ID, $sop: Date!, $eop: Date!, $description: String, $usedVolume: Boolean, $isVolumeInVehicles: Boolean) {
      updateCustomerVolume(id: $id, derivative: $derivative, projectPhaseType: $projectPhaseType, sop: $sop, eop: $eop, description: $description, usedVolume: $usedVolume, isVolumeInVehicles: $isVolumeInVehicles) {
        success
        CustomerVolume { id sop eop }
      }
    }
  `;

  const CURVE_POINT_MUTATIONS = {
    create: `
      mutation CreateCurvePoint($customerVolume: ID!, $volumeDate: Date!, $volume: Int!) {
        createCustomerVolumeCurvePoint(customerVolume: $customerVolume, volumeDate: $volumeDate, volume: $volume) {
          success
          CustomerVolumeCurvePoint { id volumeDate volume }
        }
      }
    `,
    update: `
      mutation UpdateCurvePoint($id: Int!, $customerVolume: ID!, $volumeDate: Date!, $volume: Int!) {
        updateCustomerVolumeCurvePoint(id: $id, customerVolume: $customerVolume, volumeDate: $volumeDate, volume: $volume) {
          success
          CustomerVolumeCurvePoint { id volumeDate volume }
        }
      }
    `,
    delete: `
      mutation DeleteCurvePoint($id: Int!) {
        deleteCustomerVolumeCurvePoint(id: $id) {
          success
          CustomerVolumeCurvePoint { id }
        }
      }
    `,
  };

  const subscriptionClient = createSubscriptionClient({
    onStatus(status) {
      refs.wsStatus.textContent = `WS: ${status}`;
    },
  });

  const subscriptions = {
    project: null,
    customer: null,
    derivative: new Map(),
    volume: new Map(),
    curvePoint: new Map(),
  };

  function setStatus(message, kind) {
    refs.formStatus.textContent = message;
    refs.formStatus.className = `status ${kind || ""}`.trim();
  }

  function addActivity(message) {
    const item = document.createElement("p");
    item.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    refs.activityLog.prepend(item);
    while (refs.activityLog.children.length > 24) {
      refs.activityLog.removeChild(refs.activityLog.lastChild);
    }
  }

  function normalizeOptionalId(value) {
    return value ? String(value) : null;
  }

  function derivatives() {
    return state.project?.derivativeList?.items || [];
  }

  function allVolumes() {
    const list = [];
    for (const derivative of derivatives()) {
      for (const volume of derivative.customervolumeList?.items || []) {
        list.push({
          ...volume,
          derivativeId: derivative.id,
          derivativeName: derivative.name,
          curvePoints: volume.customervolumecurvepointList?.items || [],
        });
      }
    }
    return list;
  }

  function selectedDerivative() {
    const items = derivatives();
    return items.find((item) => String(item.id) === String(state.selectedDerivativeId)) || null;
  }

  function selectedVolume() {
    const items = allVolumes();
    return items.find((item) => String(item.id) === String(state.selectedVolumeId)) || null;
  }

  function pointsForCurve(points) {
    return (points || [])
      .map((point) => ({
        date: point.volumeDate || point.date || "",
        value: Number(point.volume ?? point.value ?? 0),
      }))
      .filter((point) => point.date);
  }

  function option(label, value, selected) {
    const isSelected = String(value) === String(selected ?? "");
    return `<option value="${value}" ${isSelected ? "selected" : ""}>${label}</option>`;
  }

  function fillSelect(selectId, items, mapLabel, selectedValue, includeBlank) {
    const node = document.getElementById(selectId);
    const options = [];
    if (includeBlank) {
      options.push('<option value="">--</option>');
    }
    for (const item of items) {
      options.push(option(mapLabel(item), item.id, selectedValue));
    }
    node.innerHTML = options.join("");
  }

  function renderCurve(curveJson) {
    let points = [];
    try {
      points = JSON.parse(curveJson || "[]");
    } catch (_err) {
      points = [];
    }
    if (!Array.isArray(points) || points.length === 0) {
      refs.volumeChart.innerHTML = '<p class="placeholder">No volume curve data available.</p>';
      return;
    }

    const totalValues = points.map((item) => Number(item.total_volume ?? 0));
    const usedValues = points.map((item) => Number(item.used_volume ?? 0));
    const values = totalValues.some((value) => value > 0) ? totalValues : usedValues;
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const width = 900;
    const height = 220;
    const padX = 24;
    const padY = 20;
    const innerW = width - padX * 2;
    const innerH = height - padY * 2;
    const denom = Math.max(1, values.length - 1);

    const coords = values.map((value, index) => {
      const x = padX + (index / denom) * innerW;
      const y = padY + (1 - value / max) * innerH;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    });

    refs.volumeChart.innerHTML = `
      <div class="curve-meta">
        <span>points: <strong>${values.length}</strong></span>
        <span>min: <strong>${min.toLocaleString()}</strong></span>
        <span>max: <strong>${max.toLocaleString()}</strong></span>
      </div>
      <svg class="curve-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Project volume curve">
        <polyline points="${coords.join(" ")}" fill="none" stroke="#0f7b6c" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></polyline>
      </svg>
    `;
  }

  function renderLineCurveInto(node, points, options = {}) {
    const curvePoints = pointsForCurve(points);
    if (!curvePoints.length) {
      node.innerHTML = '<p class="placeholder">No curve points.</p>';
      return;
    }
    const ordered = [...curvePoints].sort((a, b) => (a.date < b.date ? -1 : 1));
    const values = ordered.map((point) => point.value);
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const width = options.width || 700;
    const height = options.height || 120;
    const padX = options.padX || 10;
    const padY = options.padY || 10;
    const innerW = width - padX * 2;
    const innerH = height - padY * 2;
    const denom = Math.max(1, values.length - 1);
    const coords = values.map((value, index) => {
      const x = padX + (index / denom) * innerW;
      const y = padY + (1 - value / max) * innerH;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    });
    node.innerHTML = `
      <div class="curve-meta">
        <span>points: <strong>${values.length}</strong></span>
        <span>min: <strong>${min.toLocaleString()}</strong></span>
        <span>max: <strong>${max.toLocaleString()}</strong></span>
      </div>
      <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Volume curve">
        <polyline points="${coords.join(" ")}" fill="none" stroke="#0f7b6c" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"></polyline>
      </svg>
    `;
  }

  function derivativeCurveMarkup(derivative) {
    const pointTotals = new Map();
    for (const volume of derivative.customervolumeList?.items || []) {
      for (const point of volume.customervolumecurvepointList?.items || []) {
        const date = point.volumeDate;
        const value = Number(point.volume || 0);
        if (!date) continue;
        pointTotals.set(date, (pointTotals.get(date) || 0) + value);
      }
    }
    const orderedDates = [...pointTotals.keys()].sort();
    if (!orderedDates.length) return '<p class="placeholder">No curve points.</p>';
    const points = orderedDates.map((date) => ({ date, value: pointTotals.get(date) || 0 }));
    const container = document.createElement("div");
    container.className = "row-curve";
    renderLineCurveInto(container, points, { width: 320, height: 74, padX: 8, padY: 8 });
    return container.outerHTML;
  }

  function curvePointsFromTable() {
    const rows = [];
    for (const row of refs.curvePointTableBody.querySelectorAll("tr[data-row-kind='point']")) {
      const dateInput = row.querySelector("[data-col='date']");
      const valueInput = row.querySelector("[data-col='value']");
      rows.push({
        id: row.dataset.pointId || null,
        volumeDate: dateInput?.value || "",
        volume: Number(valueInput?.value || 0),
      });
    }
    return rows;
  }

  function renderVolumeModalCurvePreview() {
    const points = curvePointsFromTable();
    renderLineCurveInto(refs.volumeModalCurvePreview, points, {
      width: 680,
      height: 120,
      padX: 12,
      padY: 12,
    });
  }

  function renderCurvePointTable() {
    const volume = selectedVolume();
    const points = [...(volume?.curvePoints || [])].sort((a, b) =>
      a.volumeDate < b.volumeDate ? -1 : 1
    );
    refs.curvePointTableBody.innerHTML = points
      .map(
        (point) => `
      <tr data-row-kind="point" data-point-id="${point.id}">
        <td><input type="date" data-col="date" value="${point.volumeDate || ""}"></td>
        <td><input type="number" data-col="value" min="0" step="1" value="${point.volume ?? 0}"></td>
        <td>
          <button type="button" class="btn curve-row-save">Save</button>
          <button type="button" class="btn btn-danger curve-row-delete">Delete</button>
        </td>
      </tr>
    `
      )
      .join("");
    if (!points.length) {
      refs.curvePointTableBody.innerHTML =
        '<tr><td colspan="3" class="placeholder">No curve points. Add your first row.</td></tr>';
    }
    renderVolumeModalCurvePreview();
  }

  function renderCards(curveJson) {
    const project = state.project;
    if (!project) {
      refs.projectHeadline.textContent = "Project not found.";
      refs.overviewContent.innerHTML = '<p class="placeholder">No project data available.</p>';
      refs.customerContent.innerHTML = '<p class="placeholder">No customer data available.</p>';
      refs.volumeContent.innerHTML = '<p class="placeholder">No volume data available.</p>';
      refs.derivativesContent.innerHTML = '<p class="placeholder">No derivatives available.</p>';
      refs.teamContent.innerHTML = '<p class="placeholder">No team data available.</p>';
      refs.volumeChart.innerHTML = '<p class="placeholder">No volume curve data available.</p>';
      return;
    }

    refs.projectHeadline.textContent = `#${project.id} ${project.name || "(unnamed project)"}`;
    refs.projectIdChip.textContent = `Project: #${project.id}`;
    refs.customerChip.textContent = `Customer: ${project.customer?.companyName || "-"}`;

    const nomination =
      project.probabilityOfNomination == null
        ? "-"
        : `${Math.round(project.probabilityOfNomination * 100)}%`;

    refs.overviewContent.innerHTML = `
      <div class="kv-grid">
        <div class="kv"><div class="k">Phase</div><div class="v">${project.projectPhaseType?.name || "-"}</div></div>
        <div class="kv"><div class="k">Type</div><div class="v">${project.projectType?.name || "-"}</div></div>
        <div class="kv"><div class="k">Currency</div><div class="v">${project.currency?.abbreviation || project.currency?.name || "-"}</div></div>
        <div class="kv"><div class="k">Nomination</div><div class="v">${nomination}</div></div>
        <div class="kv"><div class="k">Total Volume</div><div class="v">${Number(project.totalVolume || 0).toLocaleString()}</div></div>
        <div class="kv"><div class="k">Volume Flex</div><div class="v">${project.customerVolumeFlex == null ? "-" : Number(project.customerVolumeFlex).toLocaleString()}</div></div>
      </div>
    `;

    refs.customerContent.innerHTML = `
      <div class="kv-grid">
        <div class="kv"><div class="k">Company</div><div class="v">${project.customer?.companyName || "-"}</div></div>
        <div class="kv"><div class="k">Group</div><div class="v">${project.customer?.groupName || "-"}</div></div>
        <div class="kv"><div class="k">Number</div><div class="v">${project.customer?.number ?? "-"}</div></div>
        <div class="kv"><div class="k">Key Account</div><div class="v">${project.customer?.keyAccount?.fullName || project.customer?.keyAccount?.username || "-"}</div></div>
      </div>
    `;

    const volumeEntries = allVolumes();
    refs.volumeContent.innerHTML = `
      <div class="kv-grid">
        <div class="kv"><div class="k">Derivatives</div><div class="v">${derivatives().length}</div></div>
        <div class="kv"><div class="k">Volume Entries</div><div class="v">${volumeEntries.length}</div></div>
        <div class="kv"><div class="k">Earliest SOP</div><div class="v">${project.earliestSop || "-"}</div></div>
        <div class="kv"><div class="k">Latest EOP</div><div class="v">${project.latestEop || "-"}</div></div>
      </div>
    `;

    const derivativeRows = derivatives();
    refs.derivativesContent.innerHTML = derivativeRows.length
      ? `<div class="listing">${derivativeRows
          .map(
            (item) =>
              `<div class="row" data-derivative-id="${item.id}"><strong>${item.name || "(unnamed)"}</strong><br>Type: ${item.derivativeType?.name || "-"} · Plant: ${item.Plant?.name || "-"} · Volumes: ${(item.customervolumeList?.items || []).length}${derivativeCurveMarkup(item)}<div class="row-actions"><button type="button" class="btn" data-derivative-edit="${item.id}">Edit Derivative</button><button type="button" class="btn btn-primary" data-derivative-volume-edit="${item.id}">Edit Volumes</button></div></div>`
          )
          .join("")}</div>`
      : '<p class="placeholder">No derivatives on this project.</p>';

    const teamRows = (project.projectteamList?.items || []).filter((item) => item.active);
    refs.teamContent.innerHTML = teamRows.length
      ? `<div class="listing">${teamRows
          .map(
            (item) =>
              `<div class="row"><strong>${item.projectUserRole?.name || "Role"}:</strong> ${item.responsibleUser?.fullName || item.responsibleUser?.username || "n/a"}</div>`
          )
          .join("")}</div>`
      : '<p class="placeholder">No active team assignments.</p>';

    renderCurve(curveJson);
  }

  function refillEditorControls() {
    fillSelect(
      "projectCustomer",
      state.customers,
      (item) => `${item.companyName} (${item.groupName})`,
      state.project?.customer?.id,
      false
    );
    fillSelect(
      "projectPhaseType",
      state.phaseTypes,
      (item) => item.name,
      state.project?.projectPhaseType?.id,
      false
    );
    fillSelect(
      "projectType",
      state.projectTypes,
      (item) => item.name,
      state.project?.projectType?.id,
      true
    );
    fillSelect(
      "projectCurrency",
      state.currencies,
      (item) => `${item.name} (${item.abbreviation})`,
      state.project?.currency?.id,
      false
    );

    fillSelect(
      "customerKeyAccount",
      state.users,
      (item) => `${item.fullName || item.username} (#${item.id})`,
      state.project?.customer?.keyAccount?.id,
      true
    );

    const derivativeItems = derivatives();
    if (!state.selectedDerivativeId && derivativeItems.length) {
      state.selectedDerivativeId = String(derivativeItems[0].id);
    }
    fillSelect(
      "derivativeSelect",
      derivativeItems,
      (item) => `${item.name} (#${item.id})`,
      state.selectedDerivativeId,
      false
    );

    fillSelect(
      "derivativeProject",
      [{ id: state.project?.id, name: state.project?.name }].filter((item) => item.id),
      (item) => `${item.name} (#${item.id})`,
      state.project?.id,
      false
    );
    fillSelect(
      "derivativeType",
      state.derivativeTypes,
      (item) => item.name,
      selectedDerivative()?.derivativeType?.id,
      false
    );
    fillSelect(
      "derivativePlant",
      state.plants,
      (item) => item.name,
      selectedDerivative()?.Plant?.id,
      false
    );

    const volumesAll = allVolumes();
    const volumes = state.volumeModalDerivativeId
      ? volumesAll.filter(
          (item) => String(item.derivativeId) === String(state.volumeModalDerivativeId)
        )
      : volumesAll;
    if (!state.selectedVolumeId && volumes.length) {
      state.selectedVolumeId = String(volumes[0].id);
    }
    if (
      state.selectedVolumeId &&
      !volumes.some((item) => String(item.id) === String(state.selectedVolumeId))
    ) {
      state.selectedVolumeId = volumes.length ? String(volumes[0].id) : null;
    }
    fillSelect(
      "volumeSelect",
      volumes,
      (item) => `#${item.id} ${item.derivativeName || "-"}`,
      state.selectedVolumeId,
      false
    );

    fillSelect(
      "volumeDerivative",
      derivativeItems,
      (item) => `${item.name} (#${item.id})`,
      selectedVolume()?.derivativeId || state.volumeModalDerivativeId || state.selectedDerivativeId,
      false
    );
    fillSelect(
      "volumePhaseType",
      state.phaseTypes,
      (item) => item.name,
      selectedVolume()?.projectPhaseType?.id,
      true
    );

    renderCurvePointTable();
  }

  function fillModalForms() {
    const project = state.project;
    if (!project) return;

    document.getElementById("projectName").value = project.name || "";
    document.getElementById("projectProbability").value = project.probabilityOfNomination ?? "";

    document.getElementById("customerCompany").value = project.customer?.companyName || "";
    document.getElementById("customerGroup").value = project.customer?.groupName || "";
    document.getElementById("customerNumber").value = project.customer?.number ?? "";

    const derivative = selectedDerivative();
    document.getElementById("derivativeName").value = derivative?.name || "";
    document.getElementById("derivativePieces").value = derivative?.piecesPerCarSet ?? "";
    document.getElementById("derivativeNorm").value = derivative?.normDailyQuantity ?? "";
    document.getElementById("derivativeDescription").value = derivative?.volumeDescription || "";

    const volume = selectedVolume();
    document.getElementById("volumeSop").value = volume?.sop || "";
    document.getElementById("volumeEop").value = volume?.eop || "";
    document.getElementById("volumeDescription").value = volume?.description || "";
    document.getElementById("volumeUsed").checked = Boolean(volume?.usedVolume);
    document.getElementById("volumeVehicles").checked = Boolean(volume?.isVolumeInVehicles);

  }

  function openModal(modal, trigger) {
    state.lastFocused = trigger;
    state.activeModal = modal;
    refs.backdrop.hidden = false;
    modal.hidden = false;
    const firstFocusable = modal.querySelector("input, select, textarea, button");
    firstFocusable?.focus();
  }

  function closeModal() {
    if (!state.activeModal) return;
    const closingModalId = state.activeModal.id;
    state.activeModal.hidden = true;
    refs.backdrop.hidden = true;
    state.activeModal = null;
    if (closingModalId === "volumeEditModal") {
      state.volumeModalDerivativeId = null;
    }
    state.lastFocused?.focus?.();
  }

  async function loadBootstrap() {
    const data = await executeQuery(BOOTSTRAP_QUERY, {});
    state.customers = data.customerList.items || [];
    state.users = data.userList.items || [];
    state.phaseTypes = data.projectphasetypeList.items || [];
    state.projectTypes = data.projecttypeList.items || [];
    state.currencies = data.currencyList.items || [];
    state.derivativeTypes = data.derivativetypeList.items || [];
    state.plants = data.plantList.items || [];
  }

  async function loadProjectDashboard() {
    setStatus("Loading project dashboard...", "loading");
    const data = await executeQuery(DASHBOARD_QUERY, { id: String(projectId) });
    state.project = data.project;

    if (!state.project) {
      setStatus("Project not found. Returning to selection.", "warn");
      setTimeout(() => window.location.replace("/projects/"), 800);
      return;
    }

    if (!state.selectedDerivativeId && derivatives().length) {
      state.selectedDerivativeId = String(derivatives()[0].id);
    }

    const volumes = allVolumes();
    if (!state.selectedVolumeId && volumes.length) {
      state.selectedVolumeId = String(volumes[0].id);
    }

    renderCards(data.projectvolumecurve?.curveJson || "[]");
    refillEditorControls();
    fillModalForms();
    setStatus("Dashboard loaded.", "ok");
  }

  function clearSubscriptions() {
    if (subscriptions.project) subscriptions.project.unsubscribe();
    if (subscriptions.customer) subscriptions.customer.unsubscribe();
    for (const handle of subscriptions.derivative.values()) handle.unsubscribe();
    for (const handle of subscriptions.volume.values()) handle.unsubscribe();
    for (const handle of subscriptions.curvePoint.values()) handle.unsubscribe();
    subscriptions.derivative.clear();
    subscriptions.volume.clear();
    subscriptions.curvePoint.clear();
  }

  function wireSubscriptions() {
    clearSubscriptions();

    subscriptions.project = subscriptionClient.subscribe({
      query:
        "subscription OnProject($id: ID!) { onProjectChange(id: $id) { action item { id } } }",
      variables: { id: String(projectId) },
      onNext(data) {
        const event = data?.onProjectChange;
        if (!event || event.action === "snapshot") return;
        addActivity(`Project ${event.action}`);
        loadProjectDashboard().catch((err) => setStatus(err.message, "err"));
      },
    });

    const customerId = state.project?.customer?.id;
    if (customerId) {
      subscriptions.customer = subscriptionClient.subscribe({
        query:
          "subscription OnCustomer($id: ID!) { onCustomerChange(id: $id) { action item { id } } }",
        variables: { id: String(customerId) },
        onNext(data) {
          const event = data?.onCustomerChange;
          if (!event || event.action === "snapshot") return;
          addActivity(`Customer ${event.action}`);
          loadProjectDashboard().catch((err) => setStatus(err.message, "err"));
        },
      });
    }

    for (const derivative of derivatives()) {
      const derivativeHandle = subscriptionClient.subscribe({
        query:
          "subscription OnDerivative($id: ID!) { onDerivativeChange(id: $id) { action item { id } } }",
        variables: { id: String(derivative.id) },
        onNext(data) {
          const event = data?.onDerivativeChange;
          if (!event || event.action === "snapshot") return;
          addActivity(`Derivative ${event.action}`);
          loadProjectDashboard().catch((err) => setStatus(err.message, "err"));
        },
      });
      subscriptions.derivative.set(String(derivative.id), derivativeHandle);

      for (const volume of derivative.customervolumeList?.items || []) {
        const volumeHandle = subscriptionClient.subscribe({
          query:
            "subscription OnVolume($id: ID!) { onCustomervolumeChange(id: $id) { action item { id } } }",
          variables: { id: String(volume.id) },
          onNext(data) {
            const event = data?.onCustomervolumeChange;
            if (!event || event.action === "snapshot") return;
            addActivity(`Volume ${event.action}`);
            loadProjectDashboard().catch((err) => setStatus(err.message, "err"));
          },
        });
        subscriptions.volume.set(String(volume.id), volumeHandle);

        for (const curvePoint of volume.customervolumecurvepointList?.items || []) {
          const curvePointHandle = subscriptionClient.subscribe({
            query:
              "subscription OnCurvePoint($id: ID!) { onCustomervolumecurvepointChange(id: $id) { action item { id } } }",
            variables: { id: String(curvePoint.id) },
            onNext(data) {
              const event = data?.onCustomervolumecurvepointChange;
              if (!event || event.action === "snapshot") return;
              addActivity(`Curve point ${event.action}`);
              loadProjectDashboard().catch((err) => setStatus(err.message, "err"));
            },
          });
          subscriptions.curvePoint.set(String(curvePoint.id), curvePointHandle);
        }
      }
    }
  }

  function requireValue(value, message) {
    if (String(value || "").trim()) return true;
    setStatus(message, "warn");
    return false;
  }

  async function runUpdate(
    button,
    mutation,
    variables,
    successMessage,
    options = { closeModalOnSuccess: true }
  ) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Working...";
    try {
      await executeMutation(mutation, variables);
      addActivity(successMessage);
      setStatus(successMessage, "ok");
      await loadProjectDashboard();
      wireSubscriptions();
      if (options.closeModalOnSuccess) {
        closeModal();
      } else {
        refillEditorControls();
        fillModalForms();
      }
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  function appendCurvePointRow(point = null) {
    if (!refs.curvePointTableBody) return;
    const hasPlaceholder = refs.curvePointTableBody.querySelector("tr td[colspan='3']");
    if (hasPlaceholder) {
      refs.curvePointTableBody.innerHTML = "";
    }
    const row = document.createElement("tr");
    row.dataset.rowKind = "point";
    if (point?.id) {
      row.dataset.pointId = String(point.id);
    }
    row.innerHTML = `
      <td><input type="date" data-col="date" value="${point?.volumeDate || ""}"></td>
      <td><input type="number" data-col="value" min="0" step="1" value="${point?.volume ?? 0}"></td>
      <td>
        <button type="button" class="btn curve-row-save">Save</button>
        <button type="button" class="btn btn-danger curve-row-delete">Delete</button>
      </td>
    `;
    refs.curvePointTableBody.appendChild(row);
    renderVolumeModalCurvePreview();
  }

  async function saveCurvePointRow(row, triggerButton) {
    const volume = selectedVolume();
    if (!volume) {
      setStatus("Select a volume first.", "warn");
      return false;
    }

    const dateValue = row.querySelector("[data-col='date']")?.value || "";
    const volumeValueRaw = row.querySelector("[data-col='value']")?.value || "";
    if (!requireValue(dateValue, "Curve point date is required.")) return false;
    if (!requireValue(volumeValueRaw, "Curve point volume is required.")) return false;
    const volumeValue = Number(volumeValueRaw);

    const original = triggerButton.textContent;
    triggerButton.disabled = true;
    triggerButton.textContent = "Saving...";
    try {
      const pointId = row.dataset.pointId || null;
      if (pointId) {
        await executeMutation(CURVE_POINT_MUTATIONS.update, {
          id: Number(pointId),
          customerVolume: String(volume.id),
          volumeDate: dateValue,
          volume: volumeValue,
        });
        addActivity(`Curve point #${pointId} updated.`);
      } else {
        const payload = await executeMutation(CURVE_POINT_MUTATIONS.create, {
          customerVolume: String(volume.id),
          volumeDate: dateValue,
          volume: volumeValue,
        });
        const createdId =
          payload?.createCustomerVolumeCurvePoint?.CustomerVolumeCurvePoint?.id || null;
        if (createdId != null) {
          row.dataset.pointId = String(createdId);
        }
        addActivity("Curve point created.");
      }
      return true;
    } finally {
      triggerButton.disabled = false;
      triggerButton.textContent = original;
    }
  }

  async function deleteCurvePointRow(row, triggerButton) {
    const pointId = row.dataset.pointId || null;
    if (!pointId) {
      row.remove();
      if (!refs.curvePointTableBody.querySelector("tr[data-row-kind='point']")) {
        refs.curvePointTableBody.innerHTML =
          '<tr><td colspan="3" class="placeholder">No curve points. Add your first row.</td></tr>';
      }
      renderVolumeModalCurvePreview();
      setStatus("Unsaved row removed.", "ok");
      return;
    }

    if (!window.confirm(`Delete curve point #${pointId}?`)) return;
    const original = triggerButton.textContent;
    triggerButton.disabled = true;
    triggerButton.textContent = "Deleting...";
    try {
      await executeMutation(CURVE_POINT_MUTATIONS.delete, { id: Number(pointId) });
      addActivity(`Curve point #${pointId} deleted.`);
      await loadProjectDashboard();
      wireSubscriptions();
      refillEditorControls();
      fillModalForms();
      setStatus("Curve point deleted.", "ok");
    } finally {
      triggerButton.disabled = false;
      triggerButton.textContent = original;
    }
  }

  async function saveAllCurvePointRows(button) {
    const rows = [
      ...refs.curvePointTableBody.querySelectorAll("tr[data-row-kind='point']"),
    ];
    if (!rows.length) {
      setStatus("No curve rows to save.", "warn");
      return;
    }
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Saving...";
    try {
      for (const row of rows) {
        const saveButton = row.querySelector(".curve-row-save");
        const ok = await saveCurvePointRow(row, saveButton);
        if (!ok) return;
      }
      await loadProjectDashboard();
      wireSubscriptions();
      refillEditorControls();
      fillModalForms();
      setStatus("All curve rows saved.", "ok");
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  function wireModalInteractions() {
    document.querySelectorAll("[data-close-modal]").forEach((node) => {
      node.addEventListener("click", closeModal);
    });

    refs.backdrop.addEventListener("click", closeModal);

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.activeModal) {
        closeModal();
      }
      if (event.key !== "Tab" || !state.activeModal) return;
      const focusables = state.activeModal.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
  }

  function wireActions() {
    refs.backToSelection.addEventListener("click", () => {
      window.location.href = "/projects/?restore=1";
    });

    document.getElementById("editProjectCardBtn").addEventListener("click", (event) => {
      refillEditorControls();
      fillModalForms();
      openModal(refs.projectModal, event.currentTarget);
    });

    document.getElementById("editCustomerCardBtn").addEventListener("click", (event) => {
      refillEditorControls();
      fillModalForms();
      openModal(refs.customerModal, event.currentTarget);
    });

    refs.derivativesContent.addEventListener("click", (event) => {
      const derivativeEditBtn = event.target.closest("[data-derivative-edit]");
      if (derivativeEditBtn) {
        state.selectedDerivativeId = String(derivativeEditBtn.dataset.derivativeEdit || "");
        refillEditorControls();
        fillModalForms();
        openModal(refs.derivativeModal, derivativeEditBtn);
        return;
      }
      const derivativeVolumeBtn = event.target.closest("[data-derivative-volume-edit]");
      if (derivativeVolumeBtn) {
        state.selectedDerivativeId = String(
          derivativeVolumeBtn.dataset.derivativeVolumeEdit || ""
        );
        state.volumeModalDerivativeId = state.selectedDerivativeId;
        const derivativeVolumes = allVolumes().filter(
          (item) => String(item.derivativeId) === String(state.selectedDerivativeId)
        );
        state.selectedVolumeId = derivativeVolumes.length
          ? String(derivativeVolumes[0].id)
          : null;
        refillEditorControls();
        fillModalForms();
        openModal(refs.volumeModal, derivativeVolumeBtn);
      }
    });

    document.getElementById("derivativeSelect").addEventListener("change", (event) => {
      state.selectedDerivativeId = String(event.target.value || "");
      refillEditorControls();
      fillModalForms();
    });

    document.getElementById("volumeSelect").addEventListener("change", (event) => {
      state.selectedVolumeId = String(event.target.value || "");
      refillEditorControls();
      fillModalForms();
    });

    document.getElementById("updateProjectBtn").addEventListener("click", async (event) => {
      try {
        const name = document.getElementById("projectName").value;
        const customer = document.getElementById("projectCustomer").value;
        const phaseType = document.getElementById("projectPhaseType").value;
        const currency = document.getElementById("projectCurrency").value;
        if (!requireValue(name, "Project name is required.")) return;
        if (!requireValue(customer, "Project customer is required.")) return;
        if (!requireValue(phaseType, "Project phase type is required.")) return;
        if (!requireValue(currency, "Project currency is required.")) return;

        await runUpdate(
          event.currentTarget,
          PROJECT_UPDATE_MUTATION,
          {
            id: Number(projectId),
            name,
            customer: String(customer),
            projectPhaseType: String(phaseType),
            projectType: normalizeOptionalId(document.getElementById("projectType").value),
            currency: String(currency),
            probabilityOfNomination: Number(
              document.getElementById("projectProbability").value || 0
            ),
          },
          "Project updated."
        );
      } catch (err) {
        setStatus(err.message, "err");
      }
    });

    document.getElementById("updateCustomerBtn").addEventListener("click", async (event) => {
      try {
        const companyName = document.getElementById("customerCompany").value;
        const groupName = document.getElementById("customerGroup").value;
        if (!requireValue(companyName, "Customer company name is required.")) return;
        if (!requireValue(groupName, "Customer group name is required.")) return;

        await runUpdate(
          event.currentTarget,
          CUSTOMER_UPDATE_MUTATION,
          {
            id: Number(state.project?.customer?.id),
            companyName,
            groupName,
            number: document.getElementById("customerNumber").value
              ? Number(document.getElementById("customerNumber").value)
              : null,
            keyAccount: normalizeOptionalId(document.getElementById("customerKeyAccount").value),
          },
          "Customer updated."
        );
      } catch (err) {
        setStatus(err.message, "err");
      }
    });

    document.getElementById("updateDerivativeBtn").addEventListener("click", async (event) => {
      try {
        const derivative = selectedDerivative();
        if (!derivative) return setStatus("Select a derivative first.", "warn");

        const name = document.getElementById("derivativeName").value;
        const derivativeType = document.getElementById("derivativeType").value;
        const plant = document.getElementById("derivativePlant").value;
        if (!requireValue(name, "Derivative name is required.")) return;
        if (!requireValue(derivativeType, "Derivative type is required.")) return;
        if (!requireValue(plant, "Derivative plant is required.")) return;

        await runUpdate(
          event.currentTarget,
          DERIVATIVE_UPDATE_MUTATION,
          {
            id: Number(derivative.id),
            project: String(projectId),
            name,
            derivativeType: String(derivativeType),
            Plant: String(plant),
            piecesPerCarSet: Number(document.getElementById("derivativePieces").value || 1),
            normDailyQuantity: Number(document.getElementById("derivativeNorm").value || 0),
            volumeDescription: document.getElementById("derivativeDescription").value || null,
          },
          "Derivative updated."
        );
      } catch (err) {
        setStatus(err.message, "err");
      }
    });

    document.getElementById("updateVolumeBtn").addEventListener("click", async (event) => {
      try {
        const volume = selectedVolume();
        if (!volume) return setStatus("Select a volume first.", "warn");

        const derivative = document.getElementById("volumeDerivative").value;
        const sop = document.getElementById("volumeSop").value;
        const eop = document.getElementById("volumeEop").value;
        if (!requireValue(derivative, "Volume derivative is required.")) return;
        if (!requireValue(sop, "Volume SOP is required.")) return;
        if (!requireValue(eop, "Volume EOP is required.")) return;
        if (sop > eop) return setStatus("Volume EOP must be on or after SOP.", "warn");

        await runUpdate(
          event.currentTarget,
          VOLUME_UPDATE_MUTATION,
          {
            id: Number(volume.id),
            derivative: String(derivative),
            projectPhaseType: normalizeOptionalId(document.getElementById("volumePhaseType").value),
            sop,
            eop,
            description: document.getElementById("volumeDescription").value || null,
            usedVolume: document.getElementById("volumeUsed").checked,
            isVolumeInVehicles: document.getElementById("volumeVehicles").checked,
          },
          "Volume updated.",
          { closeModalOnSuccess: false }
        );
      } catch (err) {
        setStatus(err.message, "err");
      }
    });

    refs.curvePointTableBody.addEventListener("input", (event) => {
      if (event.target.matches("[data-col='date'], [data-col='value']")) {
        renderVolumeModalCurvePreview();
      }
    });

    refs.curvePointTableBody.addEventListener("click", async (event) => {
      const saveBtn = event.target.closest(".curve-row-save");
      if (saveBtn) {
        const row = saveBtn.closest("tr[data-row-kind='point']");
        if (!row) return;
        try {
          const ok = await saveCurvePointRow(row, saveBtn);
          if (!ok) return;
          await loadProjectDashboard();
          wireSubscriptions();
          refillEditorControls();
          fillModalForms();
          setStatus("Curve row saved.", "ok");
        } catch (err) {
          setStatus(err.message, "err");
        }
        return;
      }
      const deleteBtn = event.target.closest(".curve-row-delete");
      if (deleteBtn) {
        const row = deleteBtn.closest("tr[data-row-kind='point']");
        if (!row) return;
        try {
          await deleteCurvePointRow(row, deleteBtn);
        } catch (err) {
          setStatus(err.message, "err");
        }
      }
    });

    document.getElementById("addCurvePointRowBtn").addEventListener("click", () => {
      appendCurvePointRow();
    });

    document.getElementById("saveCurvePointsBtn").addEventListener("click", async (event) => {
      try {
        await saveAllCurvePointRows(event.currentTarget);
      } catch (err) {
        setStatus(err.message, "err");
      }
    });

    document.getElementById("reloadCurvePointsBtn").addEventListener("click", () => {
      renderCurvePointTable();
      setStatus("Curve rows reloaded from server values.", "ok");
    });
  }

  async function init() {
    try {
      setStatus("Loading dashboard...", "loading");
      wireModalInteractions();
      wireActions();
      subscriptionClient.connect();
      await loadBootstrap();
      await loadProjectDashboard();
      wireSubscriptions();
      addActivity(`Dashboard opened for project #${projectId}.`);
    } catch (err) {
      setStatus(err.message || String(err), "err");
    }
  }

  window.addEventListener("beforeunload", () => {
    clearSubscriptions();
    subscriptionClient.close();
  });

  init();
})();
