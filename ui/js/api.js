// Same-origin calls to the Samvidhan API (spec: api-sessions-memory §3.4). Errors use the API envelope.

export class ApiError extends Error {
  constructor(code, message, { status = 0, retryAfter = null, requestId = null } = {}) {
    super(message);
    this.code = code;
    this.status = status;
    this.retryAfter = retryAfter;
    this.requestId = requestId;
  }
}

export async function toApiError(response) {
  let code = "HTTP_" + response.status;
  let message = response.statusText || "Request failed";
  let requestId = response.headers.get("X-Request-ID");
  try {
    const body = await response.json();
    if (body && body.error) {
      code = body.error.code;
      message = body.error.message;
      requestId = body.error.request_id || requestId;
    }
  } catch { /* not JSON */ }
  const retry = Number(response.headers.get("Retry-After"));
  return new ApiError(code, message, { status: response.status, retryAfter: Number.isFinite(retry) && retry > 0 ? retry : null, requestId });
}

async function call(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("NETWORK", "Can't reach the server. Check your connection.");
  }
  if (!response.ok) throw await toApiError(response);
  return response.status === 204 ? null : response.json();
}

export const api = {
  createSession: () => call("POST", "/v1/sessions"),
  deleteSession: (id) => call("DELETE", `/v1/sessions/${encodeURIComponent(id)}`),
  messages: (id, limit = 50) => call("GET", `/v1/sessions/${encodeURIComponent(id)}/messages?limit=${limit}`),
  feedback: (messageId, rating, comment) =>
    call("POST", `/v1/messages/${messageId}/feedback`, comment ? { rating, comment } : { rating }),
  article: (ref) => call("GET", `/v1/articles/${encodeURIComponent(ref)}`),
  meta: () => call("GET", "/v1/meta"),
};

/** Friendly copy for each API error code (spec: ui.md §3.4). */
export function friendly(error) {
  switch (error.code) {
    case "RATE_LIMITED":
      return error.retryAfter ? `Slow down a little — try again in ${error.retryAfter} s.` : error.message;
    case "SESSION_FULL":
      return "This conversation is full. Start a new chat to keep going.";
    case "MESSAGE_TOO_LONG":
      return error.message;
    case "LLM_UNAVAILABLE":
    case "BUSY":
    case "SERVICE_UNAVAILABLE":
      return "The scribe is resting — please retry in a moment.";
    case "NETWORK":
      return error.message;
    default:
      return "Something went wrong. Please try again.";
  }
}
