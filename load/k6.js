/**
 * Load profile for the documentation-first LMS.
 *
 * Two scenarios mirror the architecture targets (architecture.md §14):
 *
 *   crud  50 req/s for 60s over the student hot paths (topics, quizzes,
 *         attempts) with a P95 budget of 400 ms.
 *   rag    2 req/s for 30s against the streaming study assistant with a P95
 *         budget of 8 s.
 *
 * The API rate-limits logins to 5/min per IP and the assistant to 20/hour per
 * student, so setup signs in to five seed accounts once and the scenarios
 * reuse those sessions. That keeps the test honest: it exercises the real
 * limiters instead of disabling them.
 *
 * Run it from inside the compose network so the target is `http://api:8000`:
 *
 *   docker run --rm --network lms_default \
 *     -v "${PWD}/load:/scripts" -e BASE_URL=http://api:8000 \
 *     grafana/k6 run /scripts/k6.js --summary-export /scripts/results.json
 *
 * Against a host-exposed API instead, set BASE_URL=http://localhost:8000.
 */

import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

const CRUD_RATE = Number(__ENV.CRUD_RATE || 50);
const CRUD_DURATION = __ENV.CRUD_DURATION || "60s";
const RAG_RATE = Number(__ENV.RAG_RATE || 2);
const RAG_DURATION = __ENV.RAG_DURATION || "30s";
const RAG_START = __ENV.RAG_START || "75s";

// Optional: reuse sessions written by scripts/k6_sessions.py. A ramp of several
// runs would otherwise exhaust the 10/hour-per-account login limiter.
const SESSIONS_FILE = __ENV.SESSIONS_FILE || "";
const CACHED_SESSIONS = SESSIONS_FILE
  ? JSON.parse(open(SESSIONS_FILE)).sessions
  : null;

const ACCOUNTS = [
  "student01@lms.example.com",
  "student03@lms.example.com",
  "student05@lms.example.com",
  "student06@lms.example.com",
  "student07@lms.example.com",
].map((email) => ({ email, password: "Passw0rd!" }));

const scenarios = {
  crud: {
    executor: "constant-arrival-rate",
    rate: CRUD_RATE,
    timeUnit: "1s",
    duration: CRUD_DURATION,
    preAllocatedVUs: Math.max(10, Math.ceil(CRUD_RATE / 2)),
    maxVUs: Math.max(30, CRUD_RATE * 2),
    exec: "browse",
  },
};

const thresholds = {
  http_req_failed: ["rate<0.01"],
  "http_req_duration{scenario:crud}": ["p(95)<400"],
  checks: ["rate>0.99"],
};

// RAG_RATE=0 turns the assistant scenario off entirely so a CRUD-only
// diagnostic finishes as soon as the CRUD scenario does.
if (RAG_RATE > 0) {
  scenarios.rag = {
    executor: "constant-arrival-rate",
    rate: RAG_RATE,
    timeUnit: "1s",
    duration: RAG_DURATION,
    startTime: RAG_START,
    preAllocatedVUs: 5,
    maxVUs: 12,
    exec: "ask",
  };
  thresholds["http_req_duration{scenario:rag}"] = ["p(95)<8000"];
}

export const options = {
  scenarios,
  thresholds,
  // Setup logs in to the five seed accounts. The login limiter is 5/min per
  // IP, so a run that follows another run waits out the window instead of
  // weakening the limit.
  setupTimeout: "300s",
};

function cookieHeader(cookies) {
  return Object.entries(cookies)
    .map(([name, values]) => `${name}=${values[0].value}`)
    .join("; ");
}

function login(account) {
  const body = JSON.stringify(account);
  const params = {
    headers: { "Content-Type": "application/json" },
    tags: { name: "POST /auth/login" },
    timeout: "30s",
  };
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const res = http.post(`${BASE_URL}/api/v1/auth/login`, body, params);
    if (res.status === 200) {
      return {
        cookie: cookieHeader(res.cookies),
        csrf: res.cookies.lms_csrf[0].value,
        email: account.email,
        userId: res.json("user.id"),
      };
    }
    if (res.status !== 429 || attempt === 2) {
      throw new Error(`login ${account.email} failed: ${res.status} ${res.body}`);
    }
    // The shared login limiter is 5/min per IP; wait out the window rather
    // than weakening it for the load test.
    sleep(61);
  }
  throw new Error(`login ${account.email} stayed rate-limited`);
}

function get(path, session, tag) {
  return http.get(`${BASE_URL}${path}`, {
    headers: { Cookie: session.cookie },
    tags: { name: tag },
    timeout: "10s",
  });
}

export function setup() {
  const sessions = CACHED_SESSIONS ?? ACCOUNTS.map(login);

  // Each seed student sees a different course set (student01 has SCI101 and
  // MATH101, student03 has SCI101 only), so resolve topics per session.
  // Requesting another student's topic id would correctly return 404 and
  // pollute the latency sample.
  const perSession = sessions.map((session) => {
    const res = get("/api/v1/student/topics", session, "GET /student/topics");
    if (res.status !== 200) {
      throw new Error(`setup topics failed: ${res.status} ${res.body}`);
    }
    // A student can be enrolled in several courses, so keep the course id on
    // each topic. Pairing topics[0].course_id with a topic from another course
    // would ask the assistant about a topic outside the stated course and
    // correctly return 404.
    return res.json().map((topic) => ({ id: topic.id, courseId: topic.course_id }));
  });

  if (perSession.some((entry) => entry.length === 0)) {
    throw new Error("setup found no released topics; run scripts/demo_seed.py first");
  }

  return {
    sessions: sessions.map(({ cookie, csrf }) => ({ cookie, csrf })),
    perSession,
  };
}

export function browse(data) {
  const index = (__VU + __ITER) % data.sessions.length;
  const session = data.sessions[index];
  const topics = data.perSession[index];
  const selected = topics[__ITER % topics.length];
  const paths = [
    [
      `/api/v1/student/topics?course_id=${selected.courseId}`,
      "GET /student/topics",
    ],
    [
      `/api/v1/student/topics/${selected.id}`,
      "GET /student/topics/{id}",
    ],
    ["/api/v1/student/quizzes", "GET /student/quizzes"],
    ["/api/v1/student/attempts", "GET /student/attempts"],
  ];
  const [path, tag] = paths[__ITER % paths.length];
  const res = get(path, session, tag);
  check(res, { "status is 200": (r) => r.status === 200 });
}

export function ask(data) {
  const index = (__VU + __ITER) % data.sessions.length;
  const session = data.sessions[index];
  const topics = data.perSession[index];
  const topic = topics[__ITER % topics.length];
  const payload = JSON.stringify({
    question: "Summarise the key idea in this topic for a first-year student.",
    course_id: topic.courseId,
    topic_id: topic.id,
  });
  const res = http.post(`${BASE_URL}/api/v1/student/ask`, payload, {
    headers: {
      Cookie: session.cookie,
      "Content-Type": "application/json",
      "X-CSRF-Token": session.csrf,
    },
    tags: { name: "POST /student/assistant/ask" },
    timeout: "30s",
  });
  check(res, {
    "status is 200": (r) => r.status === 200,
    "stream completed": (r) => r.body && r.body.includes("event: done"),
  });
}
