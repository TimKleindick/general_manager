import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: path.resolve(__dirname, "../core/static/core/dashboard_app"),
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        entryFileNames: "assets/app.js",
        chunkFileNames: "assets/chunk-[name].js",
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (id.includes("@reduxjs") || id.includes("redux") || id.includes("react-redux")) {
              return "vendor-redux";
            }
            if (id.includes("recharts") || id.includes("d3-")) {
              return "vendor-charts";
            }
            return "vendor";
          }
          if (id.includes("/src/routes/DashboardPage")) return "route-dashboard";
          if (id.includes("/src/routes/ProjectsPage")) return "route-projects";
          if (id.includes("/src/components/dashboard/")) return "dashboard-components";
          if (id.includes("/src/lib/charts")) return "dashboard-charts";
          return undefined;
        },
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && assetInfo.name.endsWith(".css")) {
            return "assets/app.css";
          }
          return "assets/[name]-[hash][extname]";
        },
      },
    },
  },
});
