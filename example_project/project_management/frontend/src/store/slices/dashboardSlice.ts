import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { Project } from "@/lib/types";
import { fetchDashboardProject } from "@/store/thunks";

type ModalState = {
  project: boolean;
  customer: boolean;
  derivative: boolean;
  volume: boolean;
  team: boolean;
};

type DashboardState = {
  projectId: string | null;
  project: Project | null;
  curveJson: string;
  loading: boolean;
  selectedDerivativeId: string | null;
  selectedVolumeId: string | null;
  volumeModalDerivativeId: string | null;
  modals: ModalState;
};

const initialState: DashboardState = {
  projectId: null,
  project: null,
  curveJson: "[]",
  loading: false,
  selectedDerivativeId: null,
  selectedVolumeId: null,
  volumeModalDerivativeId: null,
  modals: { project: false, customer: false, derivative: false, volume: false, team: false },
};

const dashboardSlice = createSlice({
  name: "dashboard",
  initialState,
  reducers: {
    setProjectId(state, action: PayloadAction<string | null>) {
      state.projectId = action.payload;
    },
    setSelectedDerivativeId(state, action: PayloadAction<string | null>) {
      state.selectedDerivativeId = action.payload;
    },
    setSelectedVolumeId(state, action: PayloadAction<string | null>) {
      state.selectedVolumeId = action.payload;
    },
    setVolumeModalDerivativeId(state, action: PayloadAction<string | null>) {
      state.volumeModalDerivativeId = action.payload;
    },
    openModal(state, action: PayloadAction<keyof ModalState>) {
      state.modals[action.payload] = true;
    },
    closeModal(state, action: PayloadAction<keyof ModalState>) {
      state.modals[action.payload] = false;
      if (action.payload === "volume") {
        state.volumeModalDerivativeId = null;
      }
    },
  },
  extraReducers(builder) {
    builder
      .addCase(fetchDashboardProject.pending, (state) => {
        state.loading = true;
      })
      .addCase(fetchDashboardProject.fulfilled, (state, action) => {
        state.loading = false;
        state.project = action.payload.project;
        state.curveJson = action.payload.curveJson;
        if (!state.selectedDerivativeId && action.payload.project?.derivativeList?.items?.length) {
          state.selectedDerivativeId = String(action.payload.project.derivativeList.items[0].id);
        }
      })
      .addCase(fetchDashboardProject.rejected, (state) => {
        state.loading = false;
      });
  },
});

export const {
  setProjectId,
  setSelectedDerivativeId,
  setSelectedVolumeId,
  setVolumeModalDerivativeId,
  openModal,
  closeModal,
} = dashboardSlice.actions;

export default dashboardSlice.reducer;
