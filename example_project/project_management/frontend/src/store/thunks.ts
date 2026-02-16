import { createAsyncThunk } from "@reduxjs/toolkit";
import { executeMutation, executeQuery } from "@/lib/graphqlClient";
import type { Project } from "@/lib/types";

const PROJECT_LIST_QUERY = `
  query ProjectList($page: Int!, $pageSize: Int!, $sortBy: ProjectSortByOptions, $reverse: Boolean) {
    projectList(page: $page, pageSize: $pageSize, sortBy: $sortBy, reverse: $reverse) {
      items {
        id
        name
        totalVolume
        probabilityOfNomination { value unit }
        earliestSop
        latestEop
        projectPhaseType { id name }
        customer { id companyName groupName }
      }
      pageInfo {
        totalCount
        currentPage
        pageSize
        totalPages
      }
    }
  }
`;

const PROJECT_SEARCH_QUERY = `
  query ProjectSearch(
    $query: String!,
    $page: Int,
    $pageSize: Int,
    $sortBy: String,
    $sortDesc: Boolean
  ) {
    search(
      index: "global",
      query: $query,
      types: ["Project"],
      page: $page,
      pageSize: $pageSize,
      sortBy: $sortBy,
      sortDesc: $sortDesc
    ) {
      total
      results {
        __typename
        ... on ProjectType {
          id
          name
          totalVolume
          probabilityOfNomination { value unit }
          earliestSop
          latestEop
          projectPhaseType { id name }
          customer { id companyName groupName }
        }
      }
    }
  }
`;

const BOOTSTRAP_QUERY = `
  query DashboardBootstrapData {
    customerList(page: 1, pageSize: 300) { items { id companyName groupName number keyAccount { id fullName username } } }
    userList(page: 1, pageSize: 300) { items { id fullName username } }
    projectuserroleList(page: 1, pageSize: 100) { items { id name } }
    projectphasetypeList(page: 1, pageSize: 100) { items { id name } }
    projecttypeList(page: 1, pageSize: 100) { items { id name } }
    currencyList(page: 1, pageSize: 100) { items { id name abbreviation } }
    derivativetypeList(page: 1, pageSize: 100) { items { id name } }
    plantList(page: 1, pageSize: 100) { items { id name } }
  }
`;

const DASHBOARD_QUERY = `
  query DashboardProject($id: ID!) {
    project(id: $id) {
      id
      name
      totalVolume
      probabilityOfNomination { value unit }
      earliestSop
      latestEop
      customerVolumeFlex { value unit }
      customer { id companyName groupName number keyAccount { id fullName username } }
      projectPhaseType { id name }
      projectType { id name }
      currency { id name abbreviation }
      projectteamList(pageSize: 200) {
        items { id active projectUserRole { id name } responsibleUser { id fullName username } }
      }
      derivativeList(pageSize: 500) {
        items {
          id
          name
          derivativeType { id name }
          Plant { id name }
          piecesPerCarSet
          normDailyQuantity
          maxDailyQuantity
          volumeDescription
          customervolumeList(pageSize: 500) {
            items {
              id
              sop
              eop
              description
              usedVolume
              isVolumeInVehicles
              projectPhaseType { id name }
              customervolumecurvepointList(pageSize: 500) { items { id volumeDate volume } }
            }
          }
        }
      }
    }
    projectvolumecurve(projectId: $id) {
      curveJson
    }
  }
`;

export const fetchProjectListPage = createAsyncThunk(
  "selector/fetchProjectListPage",
  async (params: { page: number; pageSize: number; sortBy: string; reverse: boolean }) => {
    type Response = {
      projectList: {
        items: Project[];
        pageInfo: { totalCount: number; currentPage: number; pageSize: number; totalPages: number };
      };
    };
    const data = await executeQuery<Response>(PROJECT_LIST_QUERY, params);
    return {
      items: data.projectList.items,
      ...data.projectList.pageInfo,
    };
  }
);

export const fetchProjectSearchPage = createAsyncThunk(
  "selector/fetchProjectSearchPage",
  async (params: { query: string; page: number; pageSize: number; sortBy: string; reverse: boolean }) => {
    type Response = {
      search: {
        total: number;
        results: Array<Project & { __typename?: string }>;
      };
    };
    const data = await executeQuery<Response>(PROJECT_SEARCH_QUERY, {
      query: params.query,
      page: params.page,
      pageSize: params.pageSize,
      sortBy: params.sortBy,
      sortDesc: params.reverse,
    });
    const typedItems = data.search.results.filter((item) => (item.__typename || "ProjectType") === "ProjectType");
    const totalPages = params.pageSize > 0 ? Math.ceil(data.search.total / params.pageSize) : 1;
    return {
      items: typedItems,
      totalCount: data.search.total,
      currentPage: params.page,
      pageSize: params.pageSize,
      totalPages,
    };
  }
);

export const fetchCatalogs = createAsyncThunk("entities/fetchCatalogs", async () => {
  type Response = {
    customerList: { items: Array<Record<string, unknown>> };
    userList: { items: Array<Record<string, unknown>> };
    projectuserroleList: { items: Array<Record<string, unknown>> };
    projectphasetypeList: { items: Array<Record<string, unknown>> };
    projecttypeList: { items: Array<Record<string, unknown>> };
    currencyList: { items: Array<Record<string, unknown>> };
    derivativetypeList: { items: Array<Record<string, unknown>> };
    plantList: { items: Array<Record<string, unknown>> };
  };
  const data = await executeQuery<Response>(BOOTSTRAP_QUERY, {});
  return {
    customers: data.customerList.items,
    users: data.userList.items,
    projectUserRoles: data.projectuserroleList.items,
    phaseTypes: data.projectphasetypeList.items,
    projectTypes: data.projecttypeList.items,
    currencies: data.currencyList.items,
    derivativeTypes: data.derivativetypeList.items,
    plants: data.plantList.items,
  };
});

export const fetchDashboardProject = createAsyncThunk(
  "dashboard/fetchDashboardProject",
  async (projectId: string) => {
    type Response = {
      project: Project | null;
      projectvolumecurve?: { curveJson?: string | null } | null;
    };
    const data = await executeQuery<Response>(DASHBOARD_QUERY, { id: projectId });
    return {
      project: data.project,
      curveJson: data.projectvolumecurve?.curveJson || "[]",
    };
  }
);

export const mutate = createAsyncThunk(
  "dashboard/mutate",
  async ({ mutation, variables }: { mutation: string; variables: Record<string, unknown> }) => {
    return executeMutation<Record<string, unknown>>(mutation, variables);
  }
);

export const QUERIES = {
  PROJECT_LIST_QUERY,
  PROJECT_SEARCH_QUERY,
  DASHBOARD_QUERY,
};
