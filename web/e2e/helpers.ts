import { expect, type Page } from "@playwright/test";

export const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export const ACCOUNTS = {
  admin: { email: "admin@lms.example.com", password: "ChangeMe123!" },
  teacher: { email: "teacher1@lms.example.com", password: "Passw0rd!" },
  student: { email: "student01@lms.example.com", password: "Passw0rd!" },
  // Spread journeys across accounts so repeated suite runs stay inside the
  // per-account login limit (10/hour). All three are enrolled in SCI101.
  studentAlt: { email: "student05@lms.example.com", password: "Passw0rd!" },
  studentThird: { email: "student03@lms.example.com", password: "Passw0rd!" },
} as const;

export const HOME = {
  admin: "/admin/users",
  teacher: "/teacher/documents",
  student: "/student/dashboard",
} as const;

export async function login(page: Page, account: { email: string; password: string }) {
  // The API deliberately rate-limits repeated logins from one IP. A full suite
  // signs in several times per minute, so back off and retry instead of
  // weakening the limiter for tests.
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await page.getByLabel("Email").fill(account.email);
    await page.getByLabel("Password").fill(account.password);
    await page.getByRole("button", { name: "Sign in" }).click();

    const outcome = await Promise.race([
      page
        .waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 30_000 })
        .then(() => "signed-in" as const)
        .catch(() => "timeout" as const),
      page
        .getByText("Too many attempts", { exact: false })
        .waitFor({ state: "visible", timeout: 30_000 })
        .then(() => "rate-limited" as const)
        .catch(() => "timeout" as const),
    ]);
    if (outcome === "signed-in") return;
    if (outcome === "timeout") throw new Error(`Sign in as ${account.email} timed out`);
    await page.waitForTimeout(61_000);
  }
  throw new Error(`Sign in as ${account.email} stayed rate-limited`);
}

export async function logout(page: Page) {
  await page.getByRole("button", { name: "Sign out" }).first().click();
  await page.waitForURL(/\/login/, { timeout: 30_000 });
}

/**
 * Sign in through the API alone. Useful for throwaway cleanup contexts that
 * should not spend time loading a portal.
 */
export async function apiLogin(page: Page, account: { email: string; password: string }) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const response = await page.request.post(
      new URL("/api/v1/auth/login", BASE_URL).toString(),
      { data: account },
    );
    if (response.ok()) return;
    if (response.status() !== 429 || attempt === 2) {
      throw new Error(`API login failed with ${response.status()}: ${await response.text()}`);
    }
    await page.waitForTimeout(61_000);
  }
}

/**
 * Call the JSON API through the web origin so the browser's cookies and CSRF
 * token are reused. Used only for setup, polling, and cleanup; user-visible
 * behaviour is still exercised through the UI.
 */
export async function api<T = unknown>(
  page: Page,
  path: string,
  init: { method?: string; data?: unknown } = {},
): Promise<T> {
  const method = init.method ?? "GET";
  const cookies = await page.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === "lms_csrf")?.value;
  const headers: Record<string, string> = {};
  if (csrf && method !== "GET") headers["X-CSRF-Token"] = csrf;

  const response = await page.request.fetch(new URL(path, BASE_URL).toString(), {
    method,
    headers,
    data: init.data,
    timeout: 90_000,
  });

  if (!response.ok()) {
    throw new Error(
      `${method} ${path} failed with ${response.status()}: ${await response.text()}`,
    );
  }
  if (response.status() === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function poll<T>(
  read: () => Promise<T>,
  predicate: (value: T) => boolean,
  { timeout = 240_000, interval = 2_000, label = "condition" } = {},
): Promise<T> {
  const deadline = Date.now() + timeout;
  let last: T | undefined;
  while (Date.now() < deadline) {
    last = await read();
    if (predicate(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  throw new Error(`Timed out waiting for ${label}; last value: ${JSON.stringify(last)}`);
}
