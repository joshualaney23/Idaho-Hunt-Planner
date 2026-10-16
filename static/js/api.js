// Thin fetch wrapper for the JSON API (§6). Every error comes back as
// `{error: {code, message, detail}}`, which this turns into an ApiError so
// callers can show `detail` inline on the offending form field.

export class ApiError extends Error {
  constructor(message, { code = "error", detail = null, status = 0 } = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.detail = detail;
    this.status = status;
  }
  /** Field errors as a plain object: `{zone: "required for this tag type"}`. */
  get fields() {
    return this.detail && !Array.isArray(this.detail) && typeof this.detail === "object"
      ? this.detail
      : {};
  }
}

/** Send `init` and return the parsed body, throwing ApiError on failure. */
async function send(method, path, init) {
  let resp;
  try {
    resp = await fetch(path, { method, ...init });
  } catch {
    throw new ApiError("The planner server is not responding.", { code: "offline" });
  }
  let data = null;
  try {
    data = await resp.json();
  } catch {
    data = null;
  }
  if (!resp.ok) {
    const err = (data && data.error) || {};
    throw new ApiError(err.message || `${method} ${path} failed (${resp.status})`, {
      code: err.code || "error",
      detail: err.detail ?? null,
      status: resp.status,
    });
  }
  return data;
}

/** Issue a JSON request and return the parsed body, throwing ApiError on failure. */
export function request(method, path, body) {
  return send(method, path, {
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/**
 * POST a File as multipart/form-data — the PDF upload (§6, T-15b). The browser
 * sets the multipart boundary, so this never sets Content-Type itself. Extra
 * `fields` become form fields (e.g. the `kind`/`year` classifier overrides).
 */
export function postFile(path, file, fields = {}) {
  const form = new FormData();
  form.append("file", file);
  for (const [k, v] of Object.entries(fields)) if (v != null) form.append(k, String(v));
  return send("POST", path, { body: form });
}

export const getJSON = (path) => request("GET", path);
export const postJSON = (path, body) => request("POST", path, body ?? {});
export const putJSON = (path, body) => request("PUT", path, body ?? {});
export const deleteJSON = (path) => request("DELETE", path);
