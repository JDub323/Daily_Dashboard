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

/**
 * `fetch`, with the one failure it reports uselessly translated.
 *
 * A server that is not running makes fetch reject with a bare TypeError whose
 * message is "Failed to fetch" — and that string was what the panel then
 * displayed. It names nothing and suggests nothing, for far and away the most
 * likely cause: the dashboard process has stopped. Saying so turns a mystery
 * into an instruction.
 *
 * `cause` is kept so the original is still in the console for anything that is
 * genuinely not a dead server (a CORS refusal, an aborted request).
 */
async function send(path, init) {
  try {
    return await fetch(path, init);
  } catch (cause) {
    throw new ApiError("cannot reach the dashboard server — is it still running?", { cause });
  }
}

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

/**
 * `headers` exists for exactly one caller: the portfolio panel's unlock token.
 *
 * It is passed per request rather than held here because that is the property
 * worth having — a token in this module, or in a cookie, would ride along on
 * every call the dashboard makes and end up in the access log. The panel keeps
 * it in one closure and attaches it to the two requests that need it.
 */
export async function get(path, params = {}, headers = null) {
  const query = new URLSearchParams(params).toString();
  return unwrap(await send(query ? `${path}?${query}` : path, headers ? { headers } : undefined));
}

/** A dated POST — `date` is filled in from the browser unless supplied. */
export function post(path, body = {}, headers = null) {
  return postRaw(path, { date: today(), ...body }, headers);
}

/** For the endpoints whose bodies have no `date` field; they forbid extras. */
export async function postRaw(path, body = {}, headers = null) {
  return unwrap(
    await send(path, {
      method: "POST",
      headers: { "content-type": "application/json", ...headers },
      body: JSON.stringify(body),
    }),
  );
}

// There was a `stream()` here — a POST that read newline-delimited JSON,
// {status} lines then {problem}. Its one caller was the math problem's paid
// generation, which does not exist any more: the problem comes from the
// overnight batch or the curated pool, both of them a file read, so there is
// nothing left in the app slow enough to need progress lines.
