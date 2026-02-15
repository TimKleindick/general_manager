import { createSlice, type PayloadAction } from "@reduxjs/toolkit";
import type { Project } from "@/lib/types";
import { fetchProjectListPage, fetchProjectSearchPage } from "@/store/thunks";

type SelectorState = {
  items: Project[];
  page: number;
  hasNext: boolean;
  loading: boolean;
  total: number;
  sortBy: string;
  reverse: boolean;
  query: string;
  restore: {
    loadedPages: number;
    scrollY: number;
  };
};

const initialState: SelectorState = {
  items: [],
  page: 1,
  hasNext: true,
  loading: false,
  total: 0,
  sortBy: "name",
  reverse: false,
  query: "",
  restore: {
    loadedPages: 1,
    scrollY: 0,
  },
};

const selectorSlice = createSlice({
  name: "selector",
  initialState,
  reducers: {
    setSortBy(state, action: PayloadAction<string>) {
      state.sortBy = action.payload;
      state.page = 1;
      state.items = [];
      state.hasNext = true;
    },
    setReverse(state, action: PayloadAction<boolean>) {
      state.reverse = action.payload;
      state.page = 1;
      state.items = [];
      state.hasNext = true;
    },
    setRestoreState(state, action: PayloadAction<{ loadedPages: number; scrollY: number }>) {
      state.restore = action.payload;
    },
    setQuery(state, action: PayloadAction<string>) {
      state.query = action.payload;
      state.page = 1;
      state.items = [];
      state.hasNext = true;
      state.total = 0;
    },
    resetSelector(state) {
      state.items = [];
      state.page = 1;
      state.hasNext = true;
      state.total = 0;
    },
  },
  extraReducers(builder) {
    builder
      .addCase(fetchProjectListPage.pending, (state) => {
        state.loading = true;
      })
      .addCase(fetchProjectSearchPage.pending, (state) => {
        state.loading = true;
      })
      .addCase(fetchProjectListPage.fulfilled, (state, action) => {
        state.loading = false;
        state.items.push(...action.payload.items);
        state.total = action.payload.totalCount;
        state.page = action.payload.currentPage + 1;
        state.hasNext = action.payload.currentPage < action.payload.totalPages;
      })
      .addCase(fetchProjectSearchPage.fulfilled, (state, action) => {
        state.loading = false;
        state.items.push(...action.payload.items);
        state.total = action.payload.totalCount;
        state.page = action.payload.currentPage + 1;
        state.hasNext = action.payload.currentPage < action.payload.totalPages;
      })
      .addCase(fetchProjectListPage.rejected, (state) => {
        state.loading = false;
        state.hasNext = false;
      })
      .addCase(fetchProjectSearchPage.rejected, (state) => {
        state.loading = false;
        state.hasNext = false;
      });
  },
});

export const { setSortBy, setReverse, setQuery, setRestoreState, resetSelector } = selectorSlice.actions;
export default selectorSlice.reducer;
