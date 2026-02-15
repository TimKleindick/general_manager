import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

export type RouteMode = "projects" | "dashboard";
export type ActivityEvent = {
  id: string;
  event: string;
  entityType: string;
  entityId: string;
  message?: string;
  timestamp: string;
};

type AppState = {
  routeMode: RouteMode;
  wsStatus: string;
  notifications: ActivityEvent[];
};

const initialState: AppState = {
  routeMode: "projects",
  wsStatus: "idle",
  notifications: [],
};

const appSlice = createSlice({
  name: "app",
  initialState,
  reducers: {
    setRouteMode(state, action: PayloadAction<RouteMode>) {
      state.routeMode = action.payload;
    },
    setWsStatus(state, action: PayloadAction<string>) {
      state.wsStatus = action.payload;
    },
    pushNotification(state, action: PayloadAction<Omit<ActivityEvent, "id" | "timestamp">>) {
      state.notifications.unshift({
        id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
        timestamp: new Date().toISOString(),
        ...action.payload,
      });
      state.notifications = state.notifications.slice(0, 40);
    },
    clearNotifications(state) {
      state.notifications = [];
    },
  },
});

export const { setRouteMode, setWsStatus, pushNotification, clearNotifications } = appSlice.actions;
export default appSlice.reducer;
