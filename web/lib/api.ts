import type { Problem } from "./types";

export class ApiError extends Error {
  status: number;
  body: Problem | null;

  constructor(status: number, detail: string, body: Problem | null) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const NO_REFRESH_PATHS = [
  "/api/v1/auth/login",
  "/api/v1/auth/refresh",
  "/api/v1/auth/csrf",
];

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  const match = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

function buildRequest(init: RequestInit): RequestInit {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (UNSAFE_METHODS.has(method)) {
    const csrf = readCookie("lms_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  return { ...init, method, headers, credentials: "include" };
}

async function toError(response: Response): Promise<ApiError> {
  let body: Problem | null = null;
  try {
    body = (await response.json()) as Problem;
  } catch {
    body = null;
  }
  return new ApiError(
    response.status,
    body?.detail ?? `Request failed with status ${response.status}`,
    body,
  );
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response = await fetch(path, buildRequest(init));

  if (response.status === 401 && !NO_REFRESH_PATHS.some((prefix) => path.startsWith(prefix))) {
    const refreshed = await fetch(
      "/api/v1/auth/refresh",
      buildRequest({ method: "POST" }),
    );
    if (refreshed.ok) {
      response = await fetch(path, buildRequest(init));
    }
  }

  if (!response.ok) {
    throw await toError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("json")) {
    return (await response.text()) as T;
  }
  return (await response.json()) as T;
}

export type SseEventHandler = (event: string, data: unknown) => void;

function dispatchSseFrame(frame: string, onEvent: SseEventHandler): void {
  let event = "message";
  const data: string[] = [];

  for (const rawLine of frame.split("\n")) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (!line || line.startsWith(":")) continue;

    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  if (data.length === 0) return;
  const raw = data.join("\n");
  let parsed: unknown = raw;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // Leave non-JSON payloads as text.
  }
  onEvent(event, parsed);
}

/**
 * POST a request and consume a `text/event-stream` response.
 * CSRF, credentials, and access-token refresh behave like `apiFetch`.
 */
export async function apiStream(
  path: string,
  init: RequestInit,
  onEvent: SseEventHandler,
): Promise<void> {
  let response = await fetch(path, buildRequest(init));

  if (response.status === 401 && !NO_REFRESH_PATHS.some((prefix) => path.startsWith(prefix))) {
    const refreshed = await fetch("/api/v1/auth/refresh", buildRequest({ method: "POST" }));
    if (refreshed.ok) {
      response = await fetch(path, buildRequest(init));
    }
  }

  if (!response.ok) {
    throw await toError(response);
  }
  if (!response.body) {
    throw new ApiError(0, "This browser cannot stream the assistant response.", null);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        dispatchSseFrame(buffer.slice(0, boundary), onEvent);
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) dispatchSseFrame(buffer, onEvent);
  } finally {
    reader.releaseLock();
  }
}

interface MultipartResponse {
  status: number;
  contentType: string;
  text: string;
}

function sendMultipart(
  path: string,
  formData: FormData,
  onProgress?: (percent: number) => void,
): Promise<MultipartResponse> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", path);
    request.withCredentials = true;
    const csrf = readCookie("lms_csrf");
    if (csrf) request.setRequestHeader("X-CSRF-Token", csrf);

    request.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    request.onload = () => {
      resolve({
        status: request.status,
        contentType: request.getResponseHeader("content-type") ?? "",
        text: request.responseText,
      });
    };
    request.onerror = () => reject(new ApiError(0, "Network error while uploading.", null));
    request.onabort = () => reject(new ApiError(0, "Upload was cancelled.", null));
    request.send(formData);
  });
}

function parseResponse<T>(response: MultipartResponse): T {
  if (!response.text) return undefined as T;
  if (response.contentType.includes("json")) {
    return JSON.parse(response.text) as T;
  }
  return response.text as T;
}

/**
 * Upload a multipart body while reporting real browser upload progress.
 * Access-token refresh and CSRF are handled the same way as ``apiFetch``.
 */
export async function apiUpload<T>(
  path: string,
  formData: FormData,
  options: { onProgress?: (percent: number) => void } = {},
): Promise<T> {
  let response = await sendMultipart(path, formData, options.onProgress);

  if (response.status === 401) {
    try {
      await apiFetch<void>("/api/v1/auth/refresh", { method: "POST" });
      response = await sendMultipart(path, formData, options.onProgress);
    } catch {
      // Keep the original 401 response so the caller sees the auth failure.
    }
  }

  if (response.status < 200 || response.status >= 300) {
    let body: Problem | null = null;
    try {
      body = JSON.parse(response.text) as Problem;
    } catch {
      body = null;
    }
    throw new ApiError(
      response.status,
      body?.detail ?? `Upload failed with status ${response.status}`,
      body,
    );
  }

  return parseResponse<T>(response);
}

export async function logout(): Promise<void> {
  await apiFetch<void>("/api/v1/auth/logout", { method: "POST" });
}
