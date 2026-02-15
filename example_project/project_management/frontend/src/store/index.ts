import { configureStore } from "@reduxjs/toolkit";
import { TypedUseSelectorHook, useDispatch, useSelector } from "react-redux";
import appReducer from "@/store/slices/appSlice";
import selectorReducer from "@/store/slices/selectorSlice";
import dashboardReducer from "@/store/slices/dashboardSlice";
import entitiesReducer from "@/store/slices/entitiesSlice";

export const store = configureStore({
  reducer: {
    app: appReducer,
    selector: selectorReducer,
    dashboard: dashboardReducer,
    entities: entitiesReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;

export const useAppDispatch = () => useDispatch<AppDispatch>();
export const useAppSelector: TypedUseSelectorHook<RootState> = useSelector;
