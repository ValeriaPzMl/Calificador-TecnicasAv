// Peticiones a nuestra API (api/main.py). Agrega el token de la sesión de
// Supabase y, si el servidor responde 401 (sesión vencida), avisa para
// regresar al login.
import { apiErrorText } from "./examForm.js";
import { log } from "./log.js";

let supabase = null; // lo pone lib/auth.jsx; null = modo local, sin sesión
let onUnauthorized = () => {};

export function configureApi(client, handleUnauthorized) {
  supabase = client;
  onUnauthorized = handleUnauthorized;
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function apiFetch(url, opts = {}) {
  const headers = new Headers(opts.headers || {});
  if (supabase) {
    const { data } = await supabase.auth.getSession(); // supabase-js lo renueva solo
    if (data.session) headers.set("Authorization", `Bearer ${data.session.access_token}`);
  }
  log.debug(opts.method || "GET", url);
  const resp = await fetch(url, { ...opts, headers });
  if (resp.status === 401 && supabase) {
    log.warn("sesión vencida o inválida en", url);
    onUnauthorized();
    throw new ApiError("Tu sesión venció; vuelve a iniciar sesión.", 401);
  }
  return resp;
}

// JSON de la respuesta, o ApiError con el mensaje del servidor.
export async function apiJson(url, opts = {}) {
  const resp = await apiFetch(url, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new ApiError(apiErrorText(data.detail, resp.status), resp.status);
  return data;
}

export function jsonBody(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

// Baja un archivo de la API (con el token) y lo guarda con su nombre.
export async function downloadFile(url, fallbackName) {
  const resp = await apiFetch(url);
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new ApiError(apiErrorText(data.detail, resp.status), resp.status);
  }
  const disposition = resp.headers.get("content-disposition") || "";
  const match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
  const name = match ? decodeURIComponent(match[1]) : fallbackName;
  const href = URL.createObjectURL(await resp.blob());
  const a = Object.assign(document.createElement("a"), { href, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
  log.info("archivo descargado:", name);
}
