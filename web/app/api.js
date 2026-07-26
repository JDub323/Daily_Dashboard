// The HTTP client, and the one place "today" is defined.
//
// The server used to stamp dt.date.today() in WSL's timezone while the client
// compared against its own local date, so any skew silently broke every "done
// today" check. The browser is now the sole authority: every dated request
// carries `date`, and the server never asks its own clock what day it is.

export const localISO = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export const today = () => localISO(new Date());

export const daysAgo = (n) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return localISO(d);
};

class ApiError extends Error {}

async function unwrap(response) {
  let body = null;
  try {
    body = await response.json();
  } catch {
    /* a non-JSON body (a proxy page, a crash) — fall through to the status */
  }
  if (response.ok) return body;
  throw new ApiError(message(body) || `${response.status} ${response.statusText}`);
}

function message(body) {
  if (!body) return null;
  if (typeof body.error === "string") return body.error;
  // FastAPI's 422: name the field rather than showing the raw validation dump.
  if (Array.isArray(body.detail)) {
    return body.detail
      .map((d) => `${(d.loc || []).slice(1).join(".") || "request"}: ${d.msg}`)
      .join("; ");
  }
  if (typeof body.detail === "string") return body.detail;
  return null;
}

export async function get(path, params = {}) {
  const query = new URLSearchParams(params).toString();
  return unwrap(await fetch(query ? `${path}?${query}` : path));
}

/** A dated POST — `date` is filled in from the browser unless supplied. */
export function post(path, body = {}) {
  return postRaw(path, { date: today(), ...body });
}

/** For the endpoints whose bodies have no `date` field; they forbid extras. */
export async function postRaw(path, body = {}) {
  return unwrap(
    await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

/**
 * POST that reads back newline-delimited JSON: {status} lines while the
 * generation runs, then {problem} or {error}.
 */
export async function stream(path, body, onStatus) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ date: today(), ...body }),
  });
  if (!response.ok) return unwrap(response); // always throws

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, cut).trim();
      buffer = buffer.slice(cut + 1);
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.status) onStatus?.(event.status);
      else if (event.error) throw new ApiError(event.error);
      else result = event;
    }
  }
  if (!result) throw new ApiError("the stream ended without a result");
  return result;
}
