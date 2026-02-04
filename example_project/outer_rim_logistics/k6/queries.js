export const QUERIES = {
  shipList: `
    query ShipList($page: Int!, $pageSize: Int!) {
      shipList(page: $page, pageSize: $pageSize, sortBy: name) {
        items {
          id
          name
          registry
          shipClass { name code }
          status { name code }
        }
        pageInfo { totalCount totalPages }
      }
    }
  `,
  shipModules: `
    query ShipModules($shipId: ID!, $page: Int!, $pageSize: Int!) {
      ship(id: $shipId) {
        id
        name
        moduleList(page: $page, pageSize: $pageSize, sortBy: life_support_uptime) {
          items {
            id
            name
            status
            lifeSupportUptime
            oxygenReserve { value unit }
            batteryCapacity { value unit }
            spec { name moduleCode hazardLimit }
          }
          pageInfo { totalCount totalPages }
        }
      }
    }
  `,
  shipCrew: `
    query ShipCrew($shipId: ID!, $page: Int!, $pageSize: Int!) {
      ship(id: $shipId) {
        id
        name
        crewmemberList(page: $page, pageSize: $pageSize, sortBy: fatigue_index) {
          items {
            id
            name
            rank
            onDuty
            medicalHold
            fatigueIndex
            clearanceLevel
            role { code name }
            assignedModule { id name }
          }
          pageInfo { totalCount totalPages }
        }
      }
    }
  `,
  shipInventory: `
    query ShipInventory($shipId: ID!, $page: Int!, $pageSize: Int!) {
      ship(id: $shipId) {
        id
        name
        moduleList(page: $page, pageSize: $pageSize) {
          items {
            id
            name
            inventoryitemList(page: 1, pageSize: 10, sortBy: serial) {
              items {
                id
                serial
                quantity
                reserved
                receivedOn
                expiresOn
                part { partNumber name reorderThreshold inStock }
              }
              pageInfo { totalCount totalPages }
            }
          }
          pageInfo { totalCount totalPages }
        }
      }
    }
  `,
  missionReadinessList: `
    query MissionReadinessList($page: Int!, $pageSize: Int!) {
      missionreadinessList(page: $page, pageSize: $pageSize) {
        items {
          ship { id name }
          asOf
          readiness { value unit }
        }
        pageInfo { totalCount totalPages }
      }
    }
  `,
  shipModuleOxygenBurn: `
    query ShipModuleOxygenBurn($page: Int!, $pageSize: Int!) {
      shipList(page: $page, pageSize: $pageSize) {
        items {
          id
          name
          moduleList(page: 1, pageSize: 5) {
            items {
              id
              name
              oxygenburnrateList(page: 1, pageSize: 1) {
                items {
                  oxygenBurn { value unit }
                }
                pageInfo { totalCount totalPages }
              }
            }
            pageInfo { totalCount totalPages }
          }
        }
        pageInfo { totalCount totalPages }
      }
    }
  `,
  heavyCalcPack: `
    query HeavyCalcPack($page: Int!, $pageSize: Int!) {
      crewreadinessList(page: $page, pageSize: $pageSize) {
        items {
          ship { id name }
          asOf
          score { value unit }
        }
        pageInfo { totalCount totalPages }
      }
      inventoryhealthList(page: $page, pageSize: $pageSize) {
        items {
          ship { id name }
          asOf
          health { value unit }
        }
        pageInfo { totalCount totalPages }
      }
      modulehealthList(page: $page, pageSize: $pageSize) {
        items {
          ship { id name }
          score { value unit }
        }
        pageInfo { totalCount totalPages }
      }
      schedulefeasibilityList(page: $page, pageSize: $pageSize) {
        items { score { value unit } }
        pageInfo { totalCount totalPages }
      }
      resupplywindowriskList(page: $page, pageSize: $pageSize) {
        items {
          ship { id name }
          risk { value unit }
        }
        pageInfo { totalCount totalPages }
      }
      shipreadinessList(page: $page, pageSize: $pageSize) {
        items {
          ship { id name }
          asOf
          readiness { value unit }
        }
        pageInfo { totalCount totalPages }
      }
      shipinventorycoverageList(page: $page, pageSize: $pageSize) {
        items {
          ship { id name }
          asOf
          coverage { value unit }
        }
        pageInfo { totalCount totalPages }
      }
      shipcrewloadList(page: $page, pageSize: $pageSize) {
        items {
          ship { id name }
          load { value unit }
        }
        pageInfo { totalCount totalPages }
      }
      shipoxygenreserveList(page: $page, pageSize: $pageSize) {
        items {
          ship { id name }
          reserve { value unit }
        }
        pageInfo { totalCount totalPages }
      }
      shipmaintenancebacklogList(page: $page, pageSize: $pageSize) {
        items {
          ship { id name }
          backlog { value unit }
        }
        pageInfo { totalCount totalPages }
      }
    }
  `,
  workorderList: `
    query WorkOrderList($page: Int!, $pageSize: Int!) {
      workorderList(page: $page, pageSize: $pageSize) {
        items { id status severity module { id ship { id } } }
        pageInfo { totalCount totalPages }
      }
    }
  `,
  inventoryitemList: `
    query InventoryItemList($page: Int!, $pageSize: Int!) {
      inventoryitemList(page: $page, pageSize: $pageSize) {
        items { id quantity location { id ship { id } } }
        pageInfo { totalCount totalPages }
      }
    }
  `,
};

export const MUTATIONS = {
  updateWorkOrderStatus: `
    mutation UpdateWorkOrderStatus($id: Int!, $status: String!) {
      updateWorkOrder(id: $id, status: $status) {
        success
        WorkOrder { id status severity module { id name } }
      }
    }
  `,
  updateInventoryQty: `
    mutation UpdateInventoryQty($id: Int!, $quantity: Int!) {
      updateInventoryItem(id: $id, quantity: $quantity) {
        success
        InventoryItem { id serial quantity part { partNumber } }
      }
    }
  `,
};

export const SUBSCRIPTIONS = {
  workorderChanges: `
    subscription WorkOrderChanges($id: ID!) {
      onWorkorderChange(id: $id) {
        action
        item { id status severity module { id ship { id } } }
      }
    }
  `,
  inventoryItemChanges: `
    subscription InventoryItemChanges($id: ID!) {
      onInventoryitemChange(id: $id) {
        action
        item { id quantity location { id ship { id } } }
      }
    }
  `,
};
