import { Suspense, lazy } from "react";
import { Provider } from "react-redux";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { store } from "@/store";
import { ActivityMonitor } from "@/components/app/ActivityMonitor";
import { ErrorToastStack } from "@/components/app/ErrorToastStack";

const ProjectsPage = lazy(() =>
  import("@/routes/ProjectsPage").then((module) => ({ default: module.ProjectsPage }))
);
const DashboardPage = lazy(() =>
  import("@/routes/DashboardPage").then((module) => ({ default: module.DashboardPage }))
);

export function App() {
  return (
    <Provider store={store}>
      <BrowserRouter>
        <ActivityMonitor />
        <ErrorToastStack />
        <Suspense fallback={<main className="mx-auto max-w-4xl p-6 text-sm text-slate-600">Loading page...</main>}>
          <Routes>
            <Route path="/projects/" element={<ProjectsPage />} />
            <Route path="/dashboard/" element={<DashboardPage />} />
            <Route path="*" element={<Navigate to="/projects/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </Provider>
  );
}
