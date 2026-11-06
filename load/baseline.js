/**
 * Diagnostic baseline: how much of the 50 req/s budget is framework/network
 * overhead versus application work?
 *
 *   PATH=/healthz          no auth, no database, no response model
 *   PATH=/api/v1/student/topics  full auth + scope + ORM path
 *
 * Run against the compose network:
 *
 *   docker run --rm --network lms_default -v "${PWD}/load:/scripts" \
 *     -e BASE_URL=http://api:8000 -e PATH_=/healthz -e RATE=50 \
 *     grafana/k6 run /scripts/baseline.js
 */

import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const PATH = __ENV.PATH_ || "/healthz";
const RATE = Number(__ENV.RATE || 50);
const DURATION = __ENV.DURATION || "20s";
const SESSIONS_FILE = __ENV.SESSIONS_FILE || "";

const sessions = SESSIONS_FILE ? JSON.parse(open(SESSIONS_FILE)).sessions : [];

export const options = {
  scenarios: {
    probe: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: Math.max(10, RATE),
      maxVUs: Math.max(30, RATE * 2),
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration": ["p(95)<400"],
  },
};

export default function () {
  const params = {};
  if (sessions.length > 0) {
    const session = sessions[(__VU + __ITER) % sessions.length];
    params.headers = {
      Cookie: session.cookie,
      "X-CSRF-Token": session.csrf,
    };
  }
  const res = http.get(`${BASE_URL}${PATH}`, params);
  check(res, { "status is 200": (r) => r.status === 200 });
}
