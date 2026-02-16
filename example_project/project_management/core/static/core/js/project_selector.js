(function () {
  "use strict";

  const { executeQuery, createSubscriptionClient } = window.GMGraphQLClient;

  const PAGE_SIZE = 25;
  const STORAGE_KEY = "pm_project_selector_state_v1";
  const params = new URLSearchParams(window.location.search);

  const list = document.getElementById("list");
  const meta = document.getElementById("meta");
  const status = document.getElementById("status");
  const sentinel = document.getElementById("sentinel");
  const sortByEl = document.getElementById("sortBy");
  const reverseEl = document.getElementById("reverse");

  const listQuery = `
    query ProjectList(
      $page: Int!,
      $pageSize: Int!,
      $sortBy: ProjectSortByOptions,
      $reverse: Boolean
    ) {
      projectList(
        page: $page,
        pageSize: $pageSize,
        sortBy: $sortBy,
        reverse: $reverse
      ) {
        items {
          id
          name
          totalVolume
          probabilityOfNomination { value unit }
          earliestSop
          latestEop
          projectPhaseType { id name }
          customer { companyName groupName }
        }
        pageInfo {
          totalCount
          currentPage
          pageSize
          totalPages
        }
      }
    }
  `;

  const detailQuery = `
    query ProjectDetail($id: ID!) {
      project(id: $id) {
        id
        name
        projectteamList(pageSize: 100) {
          items {
            active
            projectUserRole { id name }
            responsibleUser { id fullName }
          }
        }
        derivativeList(pageSize: 500) {
          items {
            id
            name
            derivativeType { id name }
            Plant { id name }
            customervolumeList(pageSize: 500) {
              items {
                sop
                eop
                usedVolume
                projectPhaseType { id name }
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

  const singleProjectQuery = `
    query ProjectSummary($id: ID!) {
      project(id: $id) {
        id
        name
        totalVolume
        probabilityOfNomination { value unit }
        earliestSop
        latestEop
        projectPhaseType { id name }
        customer { companyName groupName }
      }
    }
  `;

  let page = 1;
  let loading = false;
  let hasNext = true;
  let total = 0;

  const cardByProjectId = new Map();
  const expandedProjects = new Set();
  const visibleProjects = new Set();
  const subscriptions = new Map();

  const subscriptionClient = createSubscriptionClient({
    onStatus(nextStatus) {
      if (nextStatus === "connected") return;
      status.textContent = `Live updates: ${nextStatus}`;
    },
  });

  function readSavedState() {
    try {
      const raw = window.sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const saved = JSON.parse(raw);
      if (!saved || typeof saved !== "object") return null;
      if (Date.now() - Number(saved.timestamp || 0) > 1000 * 60 * 30) {
        return null;
      }
      return saved;
    } catch (_err) {
      return null;
    }
  }

  function writeSavedState() {
    const payload = {
      sortBy: sortByEl.value,
      reverse: reverseEl.checked,
      loadedPages: Math.max(page - 1, 1),
      scrollY: window.scrollY,
      timestamp: Date.now(),
    };
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  }

  function clearSavedState() {
    window.sessionStorage.removeItem(STORAGE_KEY);
  }

  function openDashboard(projectId) {
    writeSavedState();
    window.location.href = `/dashboard/?projectId=${encodeURIComponent(projectId)}&return=projects`;
  }

  function formatPercent(measurement) {
    const value = measurement?.value;
    if (typeof value !== "number" || Number.isNaN(value)) return "-";
    return `${Math.round(value)}%`;
  }

  function cardGridMarkup(project) {
    const prob = formatPercent(project.probabilityOfNomination);
    return `
      <div><strong>Customer</strong><br>${project.customer?.companyName || "-"} (${project.customer?.groupName || "-"})</div>
      <div><strong>Total Volume</strong><br>${Number(project.totalVolume || 0).toLocaleString()}</div>
      <div><strong>Nomination</strong><br>${prob}</div>
      <div><strong>Earliest SOP</strong><br>${project.earliestSop || "-"}</div>
      <div><strong>Latest EOP</strong><br>${project.latestEop || "-"}</div>
    `;
  }

  function itemCard(project) {
    const node = document.createElement("article");
    node.className = "card";
    node.dataset.projectId = String(project.id);

    node.innerHTML = `
      <div class="row-1">
        <h2 class="name">#${project.id} ${project.name || "(no name)"}</h2>
        <span class="badge">${project.projectPhaseType?.name || "n/a"}</span>
      </div>
      <div class="grid">${cardGridMarkup(project)}</div>
      <div class="card-actions">
        <button class="open-dashboard" type="button">Open Dashboard</button>
        <button class="details-toggle" type="button">Show details</button>
      </div>
      <div class="details" id="details-${project.id}"></div>
    `;

    const openBtn = node.querySelector(".open-dashboard");
    const toggle = node.querySelector(".details-toggle");
    const detailContainer = node.querySelector(`#details-${project.id}`);
    let detailsLoaded = false;

    openBtn.addEventListener("click", () => openDashboard(project.id));

    toggle.addEventListener("click", async () => {
      const open = detailContainer.classList.contains("open");
      if (open) {
        detailContainer.classList.remove("open");
        toggle.textContent = "Show details";
        expandedProjects.delete(String(project.id));
        syncProjectSubscriptions();
        return;
      }

      if (!detailsLoaded) {
        toggle.disabled = true;
        toggle.textContent = "Loading details...";
        try {
          const detail = await loadProjectDetail(project.id);
          renderProjectDetail(detailContainer, detail);
          detailsLoaded = true;
        } catch (err) {
          detailContainer.innerHTML = `<p class="row">Failed to load details: ${err.message}</p>`;
        } finally {
          toggle.disabled = false;
        }
      }

      detailContainer.classList.add("open");
      expandedProjects.add(String(project.id));
      syncProjectSubscriptions();
      toggle.textContent = "Hide details";
    });

    cardByProjectId.set(String(project.id), node);
    return node;
  }

  async function loadProjectDetail(projectId) {
    const payload = await executeQuery(detailQuery, { id: String(projectId) });
    return {
      project: payload.project,
      curveJson: payload.projectvolumecurve?.curveJson || "[]",
    };
  }

  function renderProjectDetail(container, detailPayload) {
    const project = detailPayload.project;
    const teams = project.projectteamList?.items || [];
    const derivatives = project.derivativeList?.items || [];
    const activeTeams = teams.filter((team) => team.active);

    const topicRows = activeTeams.length
      ? activeTeams
          .map(
            (team) =>
              `<p class="row"><strong>${team.projectUserRole?.name || "Unknown role"}:</strong> ${team.responsibleUser?.fullName || "n/a"}</p>`
          )
          .join("")
      : '<p class="row">No active topic responsibility assignments.</p>';

    const derivativeRows = derivatives.length
      ? derivatives
          .map((derivative) => {
            const volumeItems = derivative.customervolumeList?.items || [];
            const volumeRows = volumeItems.length
              ? volumeItems
                  .map(
                    (volume) =>
                      `<li>SOP ${volume.sop || "-"} · EOP ${volume.eop || "-"} · used=${volume.usedVolume ? "yes" : "no"} · phase=${volume.projectPhaseType?.name || "-"}</li>`
                  )
                  .join("")
              : "<li>No volume entries.</li>";

            return `
              <div class="row">
                <strong>${derivative.name || "(unnamed derivative)"}</strong>
                <br>Type: ${derivative.derivativeType?.name || "-"} · Plant: ${derivative.Plant?.name || "-"}
                <ul class="sub-list">${volumeRows}</ul>
              </div>
            `;
          })
          .join("")
      : '<p class="row">No derivatives on this project.</p>';

    const curveMarkup = renderCurve(detailPayload.curveJson);

    container.innerHTML = `
      <section class="curve-box">
        <h3 class="section-title">Project Volume Curve</h3>
        ${curveMarkup}
      </section>
      <section class="topic-list">
        <h3 class="section-title">Responsible By Topic</h3>
        ${topicRows}
      </section>
      <section class="derivative-list">
        <h3 class="section-title">Derivatives With SOP/EOP</h3>
        ${derivativeRows}
      </section>
    `;
  }

  function renderCurve(curveJson) {
    let points = [];
    try {
      points = JSON.parse(curveJson || "[]");
    } catch (_err) {
      points = [];
    }
    if (!Array.isArray(points) || points.length === 0) {
      return '<p class="row">No curve data available.</p>';
    }

    const totalValues = points.map((item) => Number(item.total_volume ?? 0));
    const usedValues = points.map((item) => Number(item.used_volume ?? 0));
    const values = totalValues.some((value) => value > 0) ? totalValues : usedValues;
    const max = Math.max(...values, 1);
    const min = Math.min(...values, 0);
    const width = 680;
    const height = 120;
    const padX = 14;
    const padY = 12;
    const innerW = width - padX * 2;
    const innerH = height - padY * 2;
    const denom = Math.max(1, values.length - 1);

    const coords = values.map((value, index) => {
      const x = padX + (index / denom) * innerW;
      const y = padY + (1 - value / max) * innerH;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    });

    return `
      <div class="curve-meta">
        <span><strong>Points:</strong> ${values.length}</span>
        <span><strong>Min:</strong> ${min.toLocaleString()}</span>
        <span><strong>Max:</strong> ${max.toLocaleString()}</span>
      </div>
      <svg class="curve-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Project volume curve">
        <polyline
          points="${coords.join(" ")}"
          fill="none"
          stroke="#2e6b3a"
          stroke-width="2.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    `;
  }

  async function loadNextPage() {
    if (loading || !hasNext) return;

    loading = true;
    status.textContent = "Loading more projects...";

    try {
      const payload = await executeQuery(listQuery, {
        page,
        pageSize: PAGE_SIZE,
        sortBy: sortByEl.value,
        reverse: reverseEl.checked,
      });

      const pageData = payload.projectList;
      total = pageData.pageInfo.totalCount;
      for (const project of pageData.items) {
        list.appendChild(itemCard(project));
      }

      hasNext = pageData.pageInfo.currentPage < pageData.pageInfo.totalPages;
      page += 1;

      meta.textContent = `${list.children.length} of ${total} projects loaded · page size ${PAGE_SIZE}`;
      status.textContent = hasNext ? "Scroll down to load more." : "All projects loaded.";
      syncProjectSubscriptions();
      writeSavedState();
    } catch (err) {
      status.textContent = `Failed to load projects: ${err.message}`;
      hasNext = false;
    } finally {
      loading = false;
    }
  }

  const observer = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting) {
        loadNextPage();
      }
    },
    { rootMargin: "1400px" }
  );

  function resetAndReload() {
    page = 1;
    hasNext = true;
    loading = false;
    list.innerHTML = "";
    cardByProjectId.clear();
    visibleProjects.clear();
    expandedProjects.clear();
    for (const handle of subscriptions.values()) {
      handle.unsubscribe();
    }
    subscriptions.clear();
    loadNextPage();
  }

  async function refreshProjectCard(projectId) {
    const node = cardByProjectId.get(String(projectId));
    if (!node) return;

    try {
      const result = await executeQuery(singleProjectQuery, { id: String(projectId) });
      const project = result.project;
      if (!project) return;

      const title = node.querySelector(".name");
      const badge = node.querySelector(".badge");
      const grid = node.querySelector(".grid");
      if (title) title.textContent = `#${project.id} ${project.name || "(no name)"}`;
      if (badge) badge.textContent = project.projectPhaseType?.name || "n/a";
      if (grid) grid.innerHTML = cardGridMarkup(project);
      node.classList.add("updated");
      setTimeout(() => node.classList.remove("updated"), 850);

      const details = node.querySelector(".details");
      if (details && details.classList.contains("open")) {
        const detailPayload = await loadProjectDetail(projectId);
        renderProjectDetail(details, detailPayload);
      }
    } catch (err) {
      status.textContent = `Live refresh failed for #${projectId}: ${err.message}`;
    }
  }

  function syncProjectSubscriptions() {
    const targetIds = new Set([...visibleProjects, ...expandedProjects]);

    for (const [projectId, handle] of subscriptions.entries()) {
      if (!targetIds.has(projectId)) {
        handle.unsubscribe();
        subscriptions.delete(projectId);
      }
    }

    for (const projectId of targetIds) {
      if (subscriptions.has(projectId)) continue;
      const handle = subscriptionClient.subscribe({
        query: `
          subscription OnProjectChange($id: ID!) {
            onProjectChange(id: $id) {
              action
              item { id }
            }
          }
        `,
        variables: { id: String(projectId) },
        onNext(data) {
          const payload = data?.onProjectChange;
          if (!payload) return;
          if (payload.action === "delete") {
            const card = cardByProjectId.get(String(projectId));
            card?.remove();
            cardByProjectId.delete(String(projectId));
            subscriptions.get(String(projectId))?.unsubscribe();
            subscriptions.delete(String(projectId));
            expandedProjects.delete(String(projectId));
            visibleProjects.delete(String(projectId));
            meta.textContent = `${list.children.length} of ${total} projects loaded · page size ${PAGE_SIZE}`;
            status.textContent = `Project #${projectId} deleted.`;
            return;
          }
          refreshProjectCard(projectId);
        },
        onError(errors) {
          const first = Array.isArray(errors) ? errors[0] : null;
          status.textContent = `Live subscription error: ${first?.message || "unknown"}`;
        },
      });
      subscriptions.set(projectId, handle);
    }
  }

  const cardObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const projectId = entry.target?.dataset?.projectId;
        if (!projectId) continue;
        if (entry.isIntersecting) {
          visibleProjects.add(projectId);
        } else {
          visibleProjects.delete(projectId);
        }
      }
      syncProjectSubscriptions();
    },
    { rootMargin: "200px" }
  );

  const mutationObserver = new MutationObserver(() => {
    const observed = new Set();
    for (const card of list.querySelectorAll(".card[data-project-id]")) {
      const id = card.dataset.projectId;
      if (!id) continue;
      observed.add(id);
      cardObserver.observe(card);
    }
    for (const id of [...visibleProjects]) {
      if (!observed.has(id) && !expandedProjects.has(id)) {
        visibleProjects.delete(id);
      }
    }
    syncProjectSubscriptions();
  });

  async function initList() {
    const saved = readSavedState();
    const shouldRestore = params.get("restore") === "1" && Boolean(saved);

    if (shouldRestore && saved) {
      sortByEl.value = saved.sortBy || sortByEl.value;
      reverseEl.checked = Boolean(saved.reverse);
      const targetPages = Math.max(1, Number(saved.loadedPages || 1));
      for (let i = 0; i < targetPages; i += 1) {
        if (!hasNext && i > 0) break;
        await loadNextPage();
      }
      window.requestAnimationFrame(() => {
        window.scrollTo({ top: Number(saved.scrollY || 0), behavior: "auto" });
      });
    } else {
      await loadNextPage();
      if (params.get("restore") !== "1") {
        clearSavedState();
      }
    }

    observer.observe(sentinel);
  }

  sortByEl.addEventListener("change", () => {
    clearSavedState();
    resetAndReload();
  });

  reverseEl.addEventListener("change", () => {
    clearSavedState();
    resetAndReload();
  });

  window.addEventListener("scroll", writeSavedState, { passive: true });
  window.addEventListener("beforeunload", () => {
    writeSavedState();
    cardObserver.disconnect();
    mutationObserver.disconnect();
    subscriptionClient.close();
  });

  mutationObserver.observe(list, { childList: true, subtree: true });
  subscriptionClient.connect();
  initList();
})();
