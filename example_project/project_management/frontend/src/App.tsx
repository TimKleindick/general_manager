import { Provider } from "react-redux";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { store } from "@/store";
import { ProjectsPage } from "@/routes/ProjectsPage";
import { DashboardPage } from "@/routes/DashboardPage";
import { ActivityMonitor } from "@/components/app/ActivityMonitor";

export function App() {
  return (
    <Provider store={store}>
      <BrowserRouter>
        <ActivityMonitor />
        <Routes>
          <Route path="/projects/" element={<ProjectsPage />} />
          <Route path="/dashboard/" element={<DashboardPage />} />
          <Route path="*" element={<Navigate to="/projects/" replace />} />
        </Routes>
      </BrowserRouter>
    </Provider>
  );
}
