import { createSlice } from "@reduxjs/toolkit";
import { fetchCatalogs } from "@/store/thunks";

type NamedEntity = {
  id: string | number;
  name?: string;
  abbreviation?: string;
  companyName?: string;
  groupName?: string;
  fullName?: string;
  username?: string;
};

type EntitiesState = {
  customers: NamedEntity[];
  users: NamedEntity[];
  projectUserRoles: NamedEntity[];
  phaseTypes: NamedEntity[];
  projectTypes: NamedEntity[];
  currencies: NamedEntity[];
  derivativeTypes: NamedEntity[];
  plants: NamedEntity[];
};

const initialState: EntitiesState = {
  customers: [],
  users: [],
  projectUserRoles: [],
  phaseTypes: [],
  projectTypes: [],
  currencies: [],
  derivativeTypes: [],
  plants: [],
};

const entitiesSlice = createSlice({
  name: "entities",
  initialState,
  reducers: {},
  extraReducers(builder) {
    builder.addCase(fetchCatalogs.fulfilled, (state, action) => {
      state.customers = action.payload.customers as NamedEntity[];
      state.users = action.payload.users as NamedEntity[];
      state.projectUserRoles = action.payload.projectUserRoles as NamedEntity[];
      state.phaseTypes = action.payload.phaseTypes as NamedEntity[];
      state.projectTypes = action.payload.projectTypes as NamedEntity[];
      state.currencies = action.payload.currencies as NamedEntity[];
      state.derivativeTypes = action.payload.derivativeTypes as NamedEntity[];
      state.plants = action.payload.plants as NamedEntity[];
    });
  },
});

export default entitiesSlice.reducer;
