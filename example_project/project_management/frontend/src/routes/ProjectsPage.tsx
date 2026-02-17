import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "@/store";
import { fetchOverallProjectVolume, fetchProjectListPage, fetchProjectSearchPage } from "@/store/thunks";
import { setQuery, setRestoreState, setReverse, setSortBy, resetSelector } from "@/store/slices/selectorSlice";
import { setRouteMode } from "@/store/slices/appSlice";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { Project } from "@/lib/types";

const STORAGE_KEY = "pm_project_selector_state_v2";
const PAGE_SIZE = 25;
type ViewMode = "table" | "board";

function readSavedState() {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw) as {
      sortBy: string;
      reverse: boolean;
      query?: string;
      phaseFilter?: string;
      loadedPages: number;
      scrollY: number;
      timestamp: number;
    };
    if (!saved.timestamp || Date.now() - saved.timestamp > 1000 * 60 * 30) return null;
    return saved;
  } catch {
    return null;
  }
}

export function ProjectsPage() {
  const dispatch = useAppDispatch();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { items, page, hasNext, loading, total, overallTotalVolume, overallTotalVolumeLoading, sortBy, reverse, query } =
    useAppSelector((s) => s.selector);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const filterSelectRef = useRef<HTMLSelectElement | null>(null);
  const [phaseFilter, setPhaseFilter] = useState("all");
  const viewMode: ViewMode = searchParams.get("view") === "board" ? "board" : "table";

  const setViewMode = (nextView: ViewMode) => {
    const params = new URLSearchParams(searchParams);
    params.set("view", nextView);
    setSearchParams(params, { replace: true });
  };
  const requestPage = useCallback(
    (pageToLoad: number, sortByValue: string, reverseValue: boolean, queryValue: string, phaseFilterValue: string) => {
      const trimmedQuery = queryValue.trim();
      if (trimmedQuery) {
        return dispatch(
          fetchProjectSearchPage({
            query: trimmedQuery,
            page: pageToLoad,
            pageSize: PAGE_SIZE,
            sortBy: sortByValue,
            reverse: reverseValue,
          })
        );
      }
      return dispatch(
        fetchProjectListPage({
          page: pageToLoad,
          pageSize: PAGE_SIZE,
          sortBy: sortByValue,
          reverse: reverseValue,
          phaseFilter: phaseFilterValue,
        })
      );
    },
    [dispatch]
  );

  const phaseOptions = useMemo(() => {
    const phases = new Set<string>();
    for (const project of items) phases.add(project.projectPhaseType?.name || "Unassigned");
    return ["all", ...Array.from(phases).sort((a, b) => a.localeCompare(b))];
  }, [items]);

  const filteredItems = useMemo(() => {
    if (phaseFilter === "all") return items;
    return items.filter((project) => (project.projectPhaseType?.name || "Unassigned") === phaseFilter);
  }, [items, phaseFilter]);

  const meta = useMemo(() => {
    const base = `${items.length} of ${total} projects loaded · page size ${PAGE_SIZE}`;
    const withQuery = query.trim() ? `${base} · search: "${query.trim()}"` : base;
    return phaseFilter === "all" ? withQuery : `${withQuery} · phase: ${phaseFilter}`;
  }, [items.length, phaseFilter, query, total]);

  const dashboardSummary = useMemo(() => {
    const activeKeyAccounts = new Set(
      filteredItems
        .map((project) => String(project.customer?.keyAccount?.id || ""))
        .filter((id) => id && id !== "undefined")
    ).size;
    return {
      derivatives: filteredItems.reduce((sum, project) => sum + (project.derivativeList?.items?.length || 0), 0),
      activeKeyAccounts,
      volumeEntries: filteredItems.reduce(
        (sum, project) =>
          sum +
          (project.derivativeList?.items || []).reduce(
            (count, derivative) => count + (derivative.customervolumeList?.items?.length || 0),
            0
          ),
        0
      ),
    };
  }, [filteredItems]);

  const boardLanes = useMemo(() => {
    const lanes = new Set<string>();
    for (const project of items) lanes.add(project.projectPhaseType?.name || "Unassigned");
    const sorted = Array.from(lanes).sort((a, b) => a.localeCompare(b));
    return sorted.length ? sorted : ["Unassigned"];
  }, [items]);

  const groupedByPhase = useMemo(() => {
    const buckets = new Map<string, Project[]>();
    for (const lane of boardLanes) buckets.set(lane, []);
    for (const project of filteredItems) {
      const phase = project.projectPhaseType?.name || "Unassigned";
      if (!buckets.has(phase)) buckets.set(phase, []);
      buckets.get(phase)?.push(project);
    }
    return buckets;
  }, [boardLanes, filteredItems]);

  const saveState = useCallback(() => {
    const payload = {
      sortBy,
      reverse,
      query,
      phaseFilter,
      loadedPages: Math.max(page - 1, 1),
      scrollY: window.scrollY,
      timestamp: Date.now(),
    };
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    dispatch(setRestoreState({ loadedPages: payload.loadedPages, scrollY: payload.scrollY }));
  }, [dispatch, page, phaseFilter, query, reverse, sortBy]);

  const loadNextPage = useCallback(() => {
    if (loading || !hasNext) return;
    void requestPage(page, sortBy, reverse, query, phaseFilter);
  }, [hasNext, loading, page, phaseFilter, query, requestPage, reverse, sortBy]);

  useEffect(() => {
    dispatch(setRouteMode("projects"));
    void dispatch(fetchOverallProjectVolume());
  }, [dispatch]);

  useEffect(() => {
    const onScroll = () => saveState();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [saveState]);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) loadNextPage();
      },
      { rootMargin: "1400px" }
    );
    if (sentinelRef.current) observer.observe(sentinelRef.current);
    return () => observer.disconnect();
  }, [loadNextPage]);

  useEffect(() => {
    if (items.length) return;
    const params = new URLSearchParams(location.search);
    const shouldRestore = params.get("restore") === "1";
    if (!shouldRestore) {
      window.sessionStorage.removeItem(STORAGE_KEY);
      loadNextPage();
      return;
    }

    const saved = readSavedState();
    if (!saved) {
      loadNextPage();
      return;
    }

    dispatch(setSortBy(saved.sortBy || "name"));
    dispatch(setReverse(Boolean(saved.reverse)));
    dispatch(setQuery(saved.query || ""));
    setPhaseFilter(saved.phaseFilter || "all");

    const restore = async () => {
      dispatch(resetSelector());
      const pages = Math.max(1, Number(saved.loadedPages || 1));
      for (let i = 1; i <= pages; i += 1) {
        await requestPage(i, saved.sortBy, Boolean(saved.reverse), saved.query || "", saved.phaseFilter || "all");
      }
      window.requestAnimationFrame(() => {
        window.scrollTo({ top: Number(saved.scrollY || 0), behavior: "auto" });
      });
    };

    void restore();
  }, [dispatch, items.length, loadNextPage, location.search, requestPage]);

  const onOpenDashboard = (id: string) => {
    saveState();
    navigate(`/dashboard/?projectId=${encodeURIComponent(id)}&return=projects`);
  };

  const onSortFromHeader = useCallback(
    (nextSortBy: string) => {
      const nextReverse = sortBy === nextSortBy ? !reverse : false;
      dispatch(setSortBy(nextSortBy));
      dispatch(setReverse(nextReverse));
      dispatch(resetSelector());
      window.sessionStorage.removeItem(STORAGE_KEY);
      void requestPage(1, nextSortBy, nextReverse, query, phaseFilter);
    },
    [dispatch, phaseFilter, query, requestPage, reverse, sortBy]
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const isTypingTarget =
        target?.tagName === "INPUT" || target?.tagName === "TEXTAREA" || target?.tagName === "SELECT";
      const key = event.key.toLowerCase();
      if (key === "q") {
        event.preventDefault();
        searchInputRef.current?.focus();
        return;
      }
      if (isTypingTarget) return;
      if (key === "f") {
        event.preventDefault();
        filterSelectRef.current?.focus();
        return;
      }
      if (key === "s") {
        event.preventDefault();
        onSortFromHeader("latest_eop");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onSortFromHeader]);

  return (
    <main className="mx-auto max-w-[1460px] p-4 pb-20">
      <div className="mb-4 h-2 rounded-full bg-gradient-to-r from-teal-700 via-teal-500 to-sky-500" />

      <header className="mb-3 rounded-xl border border-border bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="font-serif text-4xl leading-tight text-slate-900">/projects</h1>
            <p className="mt-1 text-sm text-slate-500">{meta}</p>
          </div>
          <Badge className="border-teal-200 bg-teal-50 text-teal-800">Operational View</Badge>
        </div>
      </header>

      <section className="mb-3 rounded-xl border border-border bg-white shadow-sm">
        <div className="flex flex-wrap items-center gap-3 border-b border-border bg-slate-50/80 px-4 py-3 text-sm text-slate-600">
          <label className="flex min-w-[280px] flex-1 items-center gap-2">
            <span>Search</span>
            <input
              ref={searchInputRef}
              className="w-full rounded-md border border-border bg-white px-2 py-1 text-foreground"
              placeholder="Project, key account, derivative..."
              value={query}
              onChange={(event) => {
                const nextQuery = event.target.value;
                dispatch(setQuery(nextQuery));
                dispatch(resetSelector());
                window.sessionStorage.removeItem(STORAGE_KEY);
                void requestPage(1, sortBy, reverse, nextQuery, phaseFilter);
              }}
            />
          </label>
          <label className="flex items-center gap-2">
            <span>Filter</span>
            <select
              ref={filterSelectRef}
              className="rounded-md border border-border bg-white px-2 py-1"
              value={phaseFilter}
              onChange={(event) => {
                const nextPhase = event.target.value;
                setPhaseFilter(nextPhase);
                dispatch(resetSelector());
                window.sessionStorage.removeItem(STORAGE_KEY);
                void requestPage(1, sortBy, reverse, query, nextPhase);
              }}
            >
              {phaseOptions.map((phase) => (
                <option key={phase} value={phase}>
                  {phase === "all" ? "all phases" : phase}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span>Sort</span>
            <select
              className="rounded-md border border-border bg-white px-2 py-1"
              value={sortBy}
              onChange={(event) => {
                dispatch(setSortBy(event.target.value));
                dispatch(resetSelector());
                window.sessionStorage.removeItem(STORAGE_KEY);
                void requestPage(1, event.target.value, reverse, query, phaseFilter);
              }}
            >
              <option value="id">id</option>
              <option value="name">name</option>
              <option value="total_volume">total_volume</option>
              <option value="probability_of_nomination">probability_of_nomination</option>
              <option value="customer_volume_flex">customer_volume_flex</option>
              <option value="earliest_sop">earliest_sop</option>
              <option value="latest_eop">latest_eop</option>
            </select>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={reverse}
              onChange={(event) => {
                dispatch(setReverse(event.target.checked));
                dispatch(resetSelector());
                window.sessionStorage.removeItem(STORAGE_KEY);
                void requestPage(1, sortBy, event.target.checked, query, phaseFilter);
              }}
            />
            Reverse
          </label>
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant={viewMode === "table" ? "default" : "outline"}
              className="h-8 px-3 py-1"
              onClick={() => setViewMode("table")}
            >
              Table
            </Button>
            <Button
              variant={viewMode === "board" ? "default" : "outline"}
              className="h-8 px-3 py-1"
              onClick={() => setViewMode("board")}
            >
              Board
            </Button>
          </div>
        </div>

        <div className="grid gap-3 p-4 md:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border border-border bg-white p-3 text-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Derivatives</p>
            <p className="mt-1 text-lg font-semibold text-slate-900">{dashboardSummary.derivatives}</p>
          </div>
          <div className="rounded-lg border border-border bg-white p-3 text-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Volume Entries</p>
            <p className="mt-1 text-lg font-semibold text-slate-900">{dashboardSummary.volumeEntries}</p>
          </div>
          <div className="rounded-lg border border-border bg-white p-3 text-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Overall Total Volume</p>
            <p className="mt-1 text-lg font-semibold text-slate-900">
              {overallTotalVolumeLoading ? "Loading..." : `${overallTotalVolume.toLocaleString()} pcs`}
            </p>
          </div>
          <div className="rounded-lg border border-border bg-white p-3 text-sm">
            <p className="text-xs uppercase tracking-wide text-slate-500">Active Key Accounts</p>
            <p className="mt-1 text-lg font-semibold text-slate-900">{dashboardSummary.activeKeyAccounts}</p>
          </div>
        </div>
      </section>

      {viewMode === "table" ? (
        <section className="overflow-hidden rounded-xl border border-border bg-white shadow-sm">
          <div className="grid grid-cols-[110px_1.3fr_130px_110px_70px_190px_80px_130px_90px_120px_130px] gap-2 border-b border-border bg-slate-50 px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
            <button type="button" className="text-left" onClick={() => onSortFromHeader("id")}>ID</button>
            <button type="button" className="text-left" onClick={() => onSortFromHeader("name")}>Project</button>
            <span>Phase</span>
            <span>Type</span>
            <span>Cur</span>
            <span>Key Account</span>
            <span>Team</span>
            <button type="button" className="text-left" onClick={() => onSortFromHeader("total_volume")}>Volume</button>
            <button type="button" className="text-left" onClick={() => onSortFromHeader("customer_volume_flex")}>Flex</button>
            <button type="button" className="text-left" onClick={() => onSortFromHeader("latest_eop")}>Updated</button>
            <span className="text-right">Action</span>
          </div>
          <div className="divide-y divide-border">
            {filteredItems.map((project, idx) => (
              <div
                key={String(project.id)}
                className={`grid grid-cols-[110px_1.3fr_130px_110px_70px_190px_80px_130px_90px_120px_130px] items-center gap-2 px-4 py-3 text-sm ${idx % 2 === 0 ? "bg-white" : "bg-slate-50/40"}`}
              >
                <span className="font-medium text-slate-800">#{project.id}</span>
                <div>
                  <p className="font-medium text-slate-900">{project.name || "(no name)"}</p>
                  <p className="text-xs text-slate-500">{project.customer?.companyName || "-"}</p>
                </div>
                <span>{project.projectPhaseType?.name || "-"}</span>
                <span>{project.projectType?.name || "-"}</span>
                <span>{project.currency?.abbreviation || "-"}</span>
                <span>
                  {project.customer?.keyAccount?.fullName || project.customer?.keyAccount?.username || "-"}
                </span>
                <span>{project.projectteamList?.items?.filter((item) => item.active).length || 0}</span>
                <span>{Number(project.totalVolume || 0).toLocaleString()} pcs</span>
                <span>{Math.round(project.customerVolumeFlex?.value || 0)}%</span>
                <span>{project.latestEop || "-"}</span>
                <div className="text-right">
                  <Button variant="default" className="h-8 px-3 py-1" onClick={() => onOpenDashboard(String(project.id))}>
                    Open
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <section className="rounded-xl border border-border bg-white p-4 shadow-sm">
          <div className="grid gap-3 xl:grid-cols-4">
            {boardLanes.map((lane) => {
              const laneItems = groupedByPhase.get(lane) || [];
              return (
                <article key={lane} className="rounded-lg border border-border bg-slate-50/70 p-3">
                  <header className="mb-2 flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-slate-700">{lane}</h3>
                    <Badge>{laneItems.length}</Badge>
                  </header>
                  <div className="grid gap-2">
                    {laneItems.length ? (
                      laneItems.map((project) => (
                        <div key={String(project.id)} className="rounded-md border border-border bg-white p-2">
                          <p className="text-sm font-semibold text-slate-900">{project.name || "(no name)"}</p>
                          <p className="mt-1 text-xs text-slate-500">
                            KA: {project.customer?.keyAccount?.fullName || project.customer?.keyAccount?.username || "-"}
                          </p>
                          <p className="text-xs text-slate-500">
                            Team {project.projectteamList?.items?.filter((item) => item.active).length || 0} •{" "}
                            {Number(project.totalVolume || 0).toLocaleString()} pcs
                          </p>
                          <Button
                            variant="outline"
                            className="mt-2 h-7 w-full px-2 py-1 text-xs"
                            onClick={() => onOpenDashboard(String(project.id))}
                          >
                            Open Dashboard
                          </Button>
                        </div>
                      ))
                    ) : (
                      <p className="rounded-md border border-dashed border-border bg-white px-2 py-4 text-center text-xs text-slate-500">
                        No projects in this lane
                      </p>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      )}

      <p className="mt-4 text-sm text-muted-foreground">
        {loading ? "Loading more projects..." : hasNext ? "Scroll down to load more." : "All projects loaded."}
      </p>
      <div ref={sentinelRef} className="h-px" aria-hidden="true" />
    </main>
  );
}
