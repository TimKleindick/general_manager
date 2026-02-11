import http from 'k6/http';
import ws from 'k6/ws';
import { check, sleep } from 'k6';
import { QUERIES, MUTATIONS, SUBSCRIPTIONS } from './queries.js';

const BASE_URL = __ENV.ORL_BASE_URL || 'https://localhost:8443';
const GRAPHQL_URL = `${BASE_URL.replace(/\/$/, '')}/graphql/`;
const WS_URL = GRAPHQL_URL.replace(/^http/, 'ws');

const READ_WEIGHT = Number(__ENV.READ_WEIGHT || 90);
const WRITE_WEIGHT = Number(__ENV.WRITE_WEIGHT || 10);
const RUN_READ_WRITE = __ENV.RUN_READ_WRITE !== 'false';
const RUN_SUBSCRIPTIONS = __ENV.RUN_SUBSCRIPTIONS !== 'false';
const HEAVY_CALC = __ENV.HEAVY_CALC === 'true';
const HEAVY_RATE = Number(__ENV.HEAVY_RATE || 0.1);
const HEAVY_PAGE_SIZE = Number(__ENV.HEAVY_PAGE_SIZE || 3);
const RNG_SEED = __ENV.K6_SEED ? Number(__ENV.K6_SEED) : null;

const USERNAME = __ENV.ORL_SUPERUSER || '';
const PASSWORD = __ENV.ORL_PASSWORD || '';

const PAGE_SIZE = Number(__ENV.PAGE_SIZE || 20);

let _rngState = Number.isFinite(RNG_SEED) ? RNG_SEED : null;

function rand() {
  if (_rngState === null) {
    return Math.random();
  }
  _rngState = (_rngState * 1664525 + 1013904223) % 4294967296;
  return _rngState / 4294967296;
}

function randomItem(list) {
  if (!list || list.length === 0) {
    return null;
  }
  return list[Math.floor(rand() * list.length)];
}

function uuidv4() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const valueRand = (rand() * 16) | 0;
    const value = char === 'x' ? valueRand : (valueRand & 0x3) | 0x8;
    return value.toString(16);
  });
}

function randomToken(length = 32) {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let out = '';
  for (let i = 0; i < length; i += 1) {
    out += chars[Math.floor(rand() * chars.length)];
  }
  return out;
}

const scenarios = {};

if (RUN_READ_WRITE) {
  scenarios.read_write_mix = {
    executor: 'constant-arrival-rate',
    rate: Number(__ENV.RATE || 30),
    timeUnit: '1s',
    duration: __ENV.DURATION || '5m',
    preAllocatedVUs: Number(__ENV.VUS || 20),
    maxVUs: Number(__ENV.MAX_VUS || 50),
    exec: 'readWriteMix',
  };
}

if (RUN_SUBSCRIPTIONS) {
  scenarios.subscription_burst = {
    executor: 'constant-vus',
    vus: Number(__ENV.SUB_VUS || 2),
    duration: __ENV.SUB_DURATION || '5m',
    exec: 'subscriptionBurst',
    startTime: '10s',
  };
}

export const options = {
  thresholds: {
    'http_req_failed{kind:graphql}': ['rate<0.01'],
    'http_req_duration{kind:graphql}': ['p(95)<800'],
    'http_req_duration{kind:graphql,op:heavyCalcPack}': ['p(95)<2000'],
  },
  scenarios,
  tlsAuth: __ENV.K6_TLS_CERT ? [{ cert: __ENV.K6_TLS_CERT, key: __ENV.K6_TLS_KEY }] : [],
  insecureSkipTLSVerify: __ENV.K6_INSECURE === 'true',
};

function graphqlRequest(query, variables, jar, csrf, extraTags = {}) {
  const payload = JSON.stringify({ query, variables });
  const headers = {
    'Content-Type': 'application/json',
    Referer: `${BASE_URL.replace(/\/$/, '')}/`,
    Origin: BASE_URL.replace(/\/$/, ''),
  };
  if (csrf) {
    headers['X-CSRFToken'] = csrf;
  }
  const res = http.post(GRAPHQL_URL, payload, {
    headers,
    jar,
    tags: { kind: 'graphql', ...extraTags },
  });
  if (!graphqlRequest._loggedFailure && res.status !== 200) {
    graphqlRequest._loggedFailure = true;
    console.error(`graphql non-200 status: ${res.status}`);
    console.error(res.body);
  }
  const errors = res.json('errors');
  const hasAppErrors = Array.isArray(errors) && errors.length > 0;
  check(res, {
    'graphql status 200': (r) => r.status === 200,
    'graphql no errors': () => !hasAppErrors,
  });
  return res;
}
graphqlRequest._loggedFailure = false;

function getCsrf(jar) {
  const csrfUrl = `${BASE_URL.replace(/\/$/, '')}/admin/login/`;
  http.get(csrfUrl, {
    headers: {
      Referer: csrfUrl,
      Origin: BASE_URL.replace(/\/$/, ''),
    },
    jar,
  });
  const cookies = jar.cookiesForURL(csrfUrl);
  let csrf = cookies && cookies.csrftoken ? cookies.csrftoken[0] : '';
  if (!csrf) {
    csrf = randomToken();
    jar.set(csrfUrl, 'csrftoken', csrf);
  }
  return csrf;
}

function loginSession(jar) {
  if (!USERNAME || !PASSWORD) {
    return '';
  }
  const loginUrl = `${BASE_URL.replace(/\/$/, '')}/admin/login/`;
  http.get(loginUrl, { headers: { Referer: loginUrl }, jar });
  const cookies = jar.cookiesForURL(loginUrl);
  const csrf = cookies && cookies.csrftoken ? cookies.csrftoken[0] : '';
  const form = {
    username: USERNAME,
    password: PASSWORD,
    csrfmiddlewaretoken: csrf,
    next: '/admin/',
  };
  const loginRes = http.post(loginUrl, form, {
    headers: {
      Referer: loginUrl,
      Origin: BASE_URL.replace(/\/$/, ''),
    },
    redirects: 0,
    jar,
  });
  if (!loginSession._loggedFailure && loginRes.status >= 400) {
    loginSession._loggedFailure = true;
    console.error(`login failed with status=${loginRes.status}`);
    console.error(loginRes.body);
  }
  const postCookies = jar.cookiesForURL(loginUrl);
  return postCookies && postCookies.csrftoken ? postCookies.csrftoken[0] : csrf;
}
loginSession._loggedFailure = false;

function pickIds(jar, csrf) {
  const shipRes = graphqlRequest(
    QUERIES.shipList,
    { page: 1, pageSize: 50 },
    jar,
    csrf,
    { op: 'shipList' }
  );
  const ships = shipRes.json('data.shipList.items') || [];
  const ship = randomItem(ships);

  const workRes = graphqlRequest(
    QUERIES.workorderList,
    { page: 1, pageSize: 50 },
    jar,
    csrf,
    { op: 'workorderList' }
  );
  const workorders = workRes.json('data.workorderList.items') || [];
  const workorder = randomItem(workorders);

  const invRes = graphqlRequest(
    QUERIES.inventoryitemList,
    { page: 1, pageSize: 50 },
    jar,
    csrf,
    { op: 'inventoryitemList' }
  );
  const items = invRes.json('data.inventoryitemList.items') || [];
  const inventory = randomItem(items);

  return {
    shipId: ship ? ship.id : null,
    workorderId: workorder ? workorder.id : null,
    inventoryId: inventory ? inventory.id : null,
  };
}

function doReadMix(jar, csrf, ids) {
  if (HEAVY_CALC && rand() < HEAVY_RATE) {
    graphqlRequest(
      QUERIES.heavyCalcPack,
      { page: 1, pageSize: HEAVY_PAGE_SIZE },
      jar,
      csrf,
      { op: 'heavyCalcPack' }
    );
    return;
  }
  const readQueries = [
    () =>
      graphqlRequest(QUERIES.shipList, { page: 1, pageSize: PAGE_SIZE }, jar, csrf, {
        op: 'shipList',
      }),
    () =>
      ids.shipId &&
      graphqlRequest(
        QUERIES.shipModules,
        { shipId: ids.shipId, page: 1, pageSize: PAGE_SIZE },
        jar,
        csrf,
        { op: 'shipModules' }
      ),
    () =>
      ids.shipId &&
      graphqlRequest(
        QUERIES.shipCrew,
        { shipId: ids.shipId, page: 1, pageSize: PAGE_SIZE },
        jar,
        csrf,
        { op: 'shipCrew' }
      ),
    () =>
      ids.shipId &&
      graphqlRequest(
        QUERIES.shipInventory,
        { shipId: ids.shipId, page: 1, pageSize: 3 },
        jar,
        csrf,
        { op: 'shipInventory' }
      ),
    () =>
      graphqlRequest(
        QUERIES.missionReadinessList,
        { page: 1, pageSize: 10 },
        jar,
        csrf,
        { op: 'missionReadinessList' }
      ),
    () =>
      graphqlRequest(
        QUERIES.shipModuleOxygenBurn,
        { page: 1, pageSize: 5 },
        jar,
        csrf,
        { op: 'shipModuleOxygenBurn' }
      ),
  ];
  const action = randomItem(readQueries);
  if (action) {
    action();
  }
}

function doWriteMix(jar, csrf, ids) {
  if (!USERNAME || !PASSWORD) {
    return;
  }
  const statusCycle = ['open', 'in_progress', 'blocked'];
  const status = statusCycle[(__VU + __ITER) % statusCycle.length];
  const quantity = ((__VU * 17 + __ITER) % 97) + 3;
  const writeOps = [
    () =>
      ids.workorderId &&
      graphqlRequest(
        MUTATIONS.updateWorkOrderStatus,
        { id: Number(ids.workorderId), status },
        jar,
        csrf,
        { op: 'updateWorkOrderStatus' }
      ),
    () =>
      ids.inventoryId &&
      graphqlRequest(
        MUTATIONS.updateInventoryQty,
        { id: Number(ids.inventoryId), quantity },
        jar,
        csrf,
        { op: 'updateInventoryQty' }
      ),
  ];
  const action = randomItem(writeOps);
  if (action) {
    action();
  }
}

export function readWriteMix() {
  const jar = http.cookieJar();
  let csrf = getCsrf(jar);
  if (USERNAME && PASSWORD) {
    csrf = loginSession(jar) || csrf;
  }
  const ids = pickIds(jar, csrf);

  const total = READ_WEIGHT + WRITE_WEIGHT;
  const roll = rand() * total;
  if (roll < READ_WEIGHT) {
    doReadMix(jar, csrf, ids);
  } else {
    doWriteMix(jar, csrf, ids);
  }
  sleep(1);
}

export function subscriptionBurst() {
  const jar = http.cookieJar();
  let csrf = getCsrf(jar);
  if (USERNAME && PASSWORD) {
    csrf = loginSession(jar) || csrf;
  }
  const ids = pickIds(jar, csrf);
  if (!ids.workorderId && !ids.inventoryId) {
    sleep(1);
    return;
  }

  const params = {
    tags: { name: 'graphql-subscription' },
    headers: {
      Referer: `${BASE_URL.replace(/\/$/, '')}/`,
      Origin: BASE_URL.replace(/\/$/, ''),
    },
  };

  const res = ws.connect(WS_URL, params, (socket) => {
    const connId = uuidv4();
    socket.on('open', () => {
      socket.send(JSON.stringify({ type: 'connection_init' }));

      if (ids.workorderId) {
        socket.send(
          JSON.stringify({
            id: connId + '-wo',
            type: 'subscribe',
            payload: {
              query: SUBSCRIPTIONS.workorderChanges,
              variables: { id: String(ids.workorderId) },
            },
          })
        );
      }
      if (ids.inventoryId) {
        socket.send(
          JSON.stringify({
            id: connId + '-inv',
            type: 'subscribe',
            payload: {
              query: SUBSCRIPTIONS.inventoryItemChanges,
              variables: { id: String(ids.inventoryId) },
            },
          })
        );
      }
    });

    socket.on('message', (data) => {
      const msg = JSON.parse(data);
      if (msg.type === 'next') {
        // simulate bursty re-fetch on subscription event
        const burst = Number(__ENV.SUB_BURST || 3);
        for (let i = 0; i < burst; i += 1) {
          graphqlRequest(
            QUERIES.missionReadinessList,
            { page: 1, pageSize: 10 },
            jar,
            csrf,
            { op: 'missionReadinessList' }
          );
          graphqlRequest(
            QUERIES.shipModuleOxygenBurn,
            { page: 1, pageSize: 5 },
            jar,
            csrf,
            { op: 'shipModuleOxygenBurn' }
          );
        }
      }
    });

    socket.setTimeout(() => {
      socket.close();
    }, 10000);
  });
  check(res, {
    'ws status 101': (r) => r && r.status === 101,
  });
  if (res && res.status !== 101 && !subscriptionBurst._loggedWsFailure) {
    subscriptionBurst._loggedWsFailure = true;
    console.error(`ws non-101 status: ${res.status}`);
    if (res.error) {
      console.error(`ws error: ${res.error}`);
    }
    if (res.body) {
      console.error(res.body);
    }
  }

  sleep(1);
}
subscriptionBurst._loggedWsFailure = false;
