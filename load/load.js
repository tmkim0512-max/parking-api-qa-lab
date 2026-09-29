// k6 load test. PROFILE=smoke (every push, 30s) | load (manual, 10->50 RPS ramp then 2 min at 50 RPS).
// Traffic mix: lot lookup 70% / reserve+cancel 20% / ETA 10% (mock route API fixed at 200 ms).
// Thresholds are the pass criteria: any breach makes k6 exit non-zero.
import http from 'k6/http';
import { check } from 'k6';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.1.0/index.js';

const SUT = __ENV.SUT_URL || 'http://127.0.0.1:8080';
const MOCK = __ENV.MOCK_URL || 'http://127.0.0.1:8081';
const PROFILE = __ENV.PROFILE || 'smoke';
const JSON_HDR = { headers: { 'Content-Type': 'application/json' } };

const scenarios = {
  smoke: { executor: 'constant-vus', vus: 1, duration: '30s' },
  load: {
    executor: 'ramping-arrival-rate', startRate: 10, timeUnit: '1s',
    preAllocatedVUs: 50, maxVUs: 200,
    stages: [{ target: 50, duration: '1m' }, { target: 50, duration: '2m' }],
  },
};

export const options = {
  scenarios: { [PROFILE]: scenarios[PROFILE] },
  // Calibrated from 3 local runs (README "Performance"): worst p95 was lot 3.6 ms, reserve 4.0 ms,
  // eta 217 ms (200 ms of which is the mock's fixed delay). Limits = ~5x worst for lot/reserve,
  // mock delay + 100 ms for eta. Re-check after the first CI runs: shared runners are slower.
  thresholds: {
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
    'http_req_duration{ep:lot}': ['p(95)<20'],
    'http_req_duration{ep:reserve}': ['p(95)<20'],
    'http_req_duration{ep:eta}': ['p(95)<300'],
  },
};

export function setup() {
  http.post(`${MOCK}/__admin/reset`);
  const stub = { method: 'GET', path: '/route', responses: [{ status: 200, delay_ms: 200,
    body: { eta_sec: 420, distance_m: 3100 } }] };
  check(http.post(`${MOCK}/__admin/stubs`, JSON.stringify(stub), JSON_HDR), { 'stub registered': (r) => r.status === 201 });
}

export default function () {
  const roll = Math.random();
  if (roll < 0.7) {
    const r = http.get(`${SUT}/lots/LOT-B`, { tags: { ep: 'lot' } });
    check(r, { 'lot 200': (x) => x.status === 200 });
  } else if (roll < 0.9) {
    // Reserve then cancel so the lot never fills up: a correct 409 must not count as a perf failure.
    const start = new Date(Date.now() + 86400000);
    const end = new Date(start.getTime() + 3600000);
    const body = JSON.stringify({ lot_id: 'LOT-B', plate: '12가3456',
      start_at: start.toISOString(), end_at: end.toISOString() });
    const r = http.post(`${SUT}/reservations`, body, { ...JSON_HDR, tags: { ep: 'reserve' } });
    if (check(r, { 'reserve 201': (x) => x.status === 201 })) {
      const d = http.del(`${SUT}/reservations/${r.json('id')}`, null, { tags: { ep: 'cancel' } });
      check(d, { 'cancel 204': (x) => x.status === 204 });
    }
  } else {
    const r = http.get(`${SUT}/eta?lot_id=LOT-B&from_lat=37.5665&from_lng=126.978`, { tags: { ep: 'eta' } });
    check(r, { 'eta from route api': (x) => x.status === 200 && x.json('source') === 'route_api' });
  }
}

export function handleSummary(data) {
  const out = { profile: PROFILE, environment: __ENV.RUN_ENV || 'unspecified', metrics: data.metrics };
  return {
    stdout: textSummary(data, { indent: ' ', enableColors: false }),
    [__ENV.SUMMARY_FILE || `reports/k6-${PROFILE}.json`]: JSON.stringify(out, null, 2),
  };
}
