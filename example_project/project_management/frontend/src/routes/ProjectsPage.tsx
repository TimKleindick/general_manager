import { useCallback, useEffect, useMemo, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAppDispatch, useAppSelector } from "@/store";
import { fetchProjectListPage, fetchProjectSearchPage } from "@/store/thunks";
import { setQuery, setRestoreState, setReverse, setSortBy, resetSelector } from "@/store/slices/selectorSlice";
import { setRouteMode } from "@/store/slices/appSlice";
import { ProjectCard } from "@/components/selector/ProjectCard";

const STORAGE_KEY = "pm_project_selector_state_v2";
const PAGE_SIZE = 25;

function readSavedState() {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw) as {
      sortBy: string;
      reverse: boolean;
      query?: string;
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
  const { items, page, hasNext, loading, total, sortBy, reverse, query } = useAppSelector((s) => s.selector);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const requestPage = useCallback(
    (pageToLoad: number, sortByValue: string, reverseValue: boolean, queryValue: string) => {
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
        })
      );
    },
    [dispatch]
  );

  const meta = useMemo(() => {
    const base = `${items.length} of ${total} projects loaded · page size ${PAGE_SIZE}`;
    return query.trim() ? `${base} · search: "${query.trim()}"` : base;
  }, [items.length, query, total]);

  const saveState = useCallback(() => {
    const payload = {
      sortBy,
      reverse,
      query,
      loadedPages: Math.max(page - 1, 1),
      scrollY: window.scrollY,
      timestamp: Date.now(),
    };
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    dispatch(setRestoreState({ loadedPages: payload.loadedPages, scrollY: payload.scrollY }));
  }, [dispatch, page, query, reverse, sortBy]);

  const loadNextPage = useCallback(() => {
    if (loading || !hasNext) return;
    void requestPage(page, sortBy, reverse, query);
  }, [hasNext, loading, page, query, requestPage, reverse, sortBy]);

  useEffect(() => {
    dispatch(setRouteMode("projects"));
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

    const restore = async () => {
      dispatch(resetSelector());
      const pages = Math.max(1, Number(saved.loadedPages || 1));
      for (let i = 1; i <= pages; i += 1) {
        await requestPage(i, saved.sortBy, Boolean(saved.reverse), saved.query || "");
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

  return (
    <main className="mx-auto max-w-6xl p-4 pb-20">
      <header className="sticky top-0 z-10 mb-3 rounded-lg border border-border bg-background/90 p-4 backdrop-blur">
        <h1 className="font-serif text-3xl">Project List</h1>
        <p className="mt-1 text-sm text-muted-foreground">{meta}</p>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
          <label className="flex min-w-[260px] items-center gap-2">
            <span>Search</span>
            <input
              className="w-full rounded-md border border-border bg-white px-2 py-1 text-foreground"
              placeholder="Project, project manager, derivative..."
              value={query}
              onChange={(event) => {
                const nextQuery = event.target.value;
                dispatch(setQuery(nextQuery));
                dispatch(resetSelector());
                window.sessionStorage.removeItem(STORAGE_KEY);
                void requestPage(1, sortBy, reverse, nextQuery);
              }}
            />
          </label>
          <label className="flex items-center gap-2">
            <span>Sort by</span>
            <select
              className="rounded-md border border-border bg-white px-2 py-1"
              value={sortBy}
              onChange={(event) => {
                dispatch(setSortBy(event.target.value));
                dispatch(resetSelector());
                window.sessionStorage.removeItem(STORAGE_KEY);
                void requestPage(1, event.target.value, reverse, query);
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
                void requestPage(1, sortBy, event.target.checked, query);
              }}
            />
            Reverse
          </label>
        </div>
      </header>

      <section className="grid gap-3">
        {items.map((project) => (
          <ProjectCard key={String(project.id)} project={project} onOpen={onOpenDashboard} />
        ))}
      </section>

      <p className="mt-4 text-sm text-muted-foreground">
        {loading ? "Loading more projects..." : hasNext ? "Scroll down to load more." : "All projects loaded."}
      </p>
      <div ref={sentinelRef} className="h-px" aria-hidden="true" />
    </main>
  );
}
