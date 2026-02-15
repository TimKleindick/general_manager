import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "@/App";
import "@/styles/globals.css";

const rootNode = document.getElementById("app-root");

if (rootNode) {
  ReactDOM.createRoot(rootNode).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
