async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const method = (init?.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-Requested-With", "CHIO-Portfolio-Assistant");
  const response = await fetch(path, { ...init, headers });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(payload !== null && typeof payload === "object" ? (payload as { detail?: string }).detail || "Request failed" : String(payload || "Request failed"));
  return payload as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, {
    method: "POST", headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }),
  put: <T>(path: string, body: unknown) => request<T>(path, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }),
  patch: <T>(path: string, body: unknown) => request<T>(path, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, file: File, fields: Record<string, string | boolean> = {}) => {
    const form = new FormData();
    form.append("file", file);
    Object.entries(fields).forEach(([key, value]) => form.append(key, String(value)));
    return request<T>(path, { method: "POST", body: form });
  },
  uploadMany: <T>(path: string, files: File[], fields: Record<string, string | boolean> = {}) => {
    const form = new FormData();
    files.forEach(file => form.append("files", file));
    form.append("relative_paths", JSON.stringify(files.map(file => file.webkitRelativePath || file.name)));
    Object.entries(fields).forEach(([key, value]) => form.append(key, String(value)));
    return request<T>(path, { method: "POST", body: form });
  },
};
