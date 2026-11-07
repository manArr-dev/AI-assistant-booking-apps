// Thin fetch wrapper for the FastAPI backend.
// The session is an httpOnly cookie the browser sends automatically; this code never sees it.
// Every request carries X-Requested-With, which the server requires on state-changing calls (CSRF).

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let unauthorizedHandler = null;

/** Called when any non-auth request returns 401 (session expired or revoked). */
export function onUnauthorized(fn) {
  unauthorizedHandler = fn;
}

function errorText(data, status) {
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((e) => `${e.loc?.[e.loc.length - 1] ?? "field"}: ${e.msg}`).join("; ");
  }
  if (status === 429) return "Too many attempts. Please wait a few minutes.";
  return `Request failed (${status}).`;
}

async function request(method, path, body) {
  const headers = { "X-Requested-With": "booking-web" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/") && unauthorizedHandler) unauthorizedHandler();
    throw new ApiError(errorText(data, res.status), res.status);
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body = {}) => request("POST", path, body),
};
