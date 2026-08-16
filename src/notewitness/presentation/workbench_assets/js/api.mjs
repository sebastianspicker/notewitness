/** Local workbench HTTP helpers shared by controller modules. */

export class RequestError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "RequestError";
    this.status = status;
  }
}

const API_PATH_ERROR = "request path must be a same-origin relative /api/... path.";
const ENCODED_PATH_SEPARATOR = /%2f|%5c/i;

function localApiPath(path) {
  if (
    typeof path !== "string"
    || !path.startsWith("/api/")
    || /[\\\\\s]/.test(path)
    || ENCODED_PATH_SEPARATOR.test(path)
  ) {
    throw new TypeError(API_PATH_ERROR);
  }
  const normalized = new URL(path, "https://notewitness.invalid");
  if (normalized.origin !== "https://notewitness.invalid" || !normalized.pathname.startsWith("/api/")) {
    throw new TypeError(API_PATH_ERROR);
  }
  return path;
}

/**
 * @param {{ state: object }} c Controller context with mutable `state`.
 */
export function createApi(c) {
  const csrf = () => c.state.data?.csrf_token || "";
  const projectSha = () => c.state.data?.project?.sha256 || "";

  function actionHeaders(contentType = "application/json") {
    return {
      "Content-Type": contentType,
      "X-NoteWitness-CSRF": csrf(),
    };
  }

  async function request(path, options = {}) {
    const response = await fetch(localApiPath(path), { credentials: "same-origin", ...options });
    if (response.ok) {
      if (response.status === 204) return null;
      return response.json().catch(() => null);
    }
    const payload = await response.json().catch(() => null);
    const detail = payload?.error || payload?.message || payload?.detail;
    throw new RequestError(
      detail ? `${response.status}: ${detail}` : `${response.status} ${response.statusText}`,
      response.status,
    );
  }

  return { RequestError, csrf, projectSha, actionHeaders, request };
}
