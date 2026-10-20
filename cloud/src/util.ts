export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  BUCKET?: R2Bucket;
  STORAGE: string;
  STORAGE_LIMIT_MB: string;
  MAX_PACKAGE_MB: string;
  S3_ENDPOINT: string;
  S3_BUCKET: string;
  S3_ACCESS_KEY_ID?: string;
  S3_SECRET_ACCESS_KEY?: string;
  STORE_REPO: string;
  GITHUB_TOKEN?: string;
  GITHUB_API?: string;
  STORE_TOKEN?: string;
  IP_SALT?: string;
}

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const MB = 1024 * 1024;
export const CHUNK_MAX = 16 * MB;

export function json(data: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}

export const now = () => Math.floor(Date.now() / 1000);

export function hex(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256hex(data: ArrayBuffer | Uint8Array | string): Promise<string> {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
  return hex(await crypto.subtle.digest("SHA-256", bytes));
}

export function b64decode(s: string): Uint8Array {
  const norm = s.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(norm + "===".slice((norm.length + 3) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

export function b64encode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

export async function readJson<T = Record<string, unknown>>(req: Request, maxBytes = 64 * 1024): Promise<{ body: T; raw: string }> {
  const raw = await req.text();
  if (raw.length > maxBytes) throw new HttpError(413, "request too large");
  try {
    return { body: JSON.parse(raw || "{}") as T, raw };
  } catch {
    throw new HttpError(400, "invalid JSON");
  }
}

export async function readBody(req: Request, max: number): Promise<ArrayBuffer> {
  const declared = Number(req.headers.get("content-length") || "0");
  if (declared > max) throw new HttpError(413, `chunk larger than ${max} bytes`);
  const buf = await req.arrayBuffer();
  if (buf.byteLength > max) throw new HttpError(413, `chunk larger than ${max} bytes`);
  return buf;
}

export async function clientId(req: Request, env: Env): Promise<string> {
  const ip = req.headers.get("cf-connecting-ip") || "local";
  return (await sha256hex(ip + (env.IP_SALT || "cherry"))).slice(0, 24);
}

/** Fixed-window limit per action and client, counted per day. */
export async function rateLimit(env: Env, action: string, who: string, perDay: number): Promise<void> {
  const day = Math.floor(Date.now() / 86_400_000);
  const key = `${action}:${who}:${day}`;
  const row = await env.DB.prepare(
    "INSERT INTO rate (key, count) VALUES (?1, 1) ON CONFLICT(key) DO UPDATE SET count = count + 1 RETURNING count",
  ).bind(key).first<{ count: number }>();
  if (row && row.count > perDay) throw new HttpError(429, `too many requests for ${action} today, try again tomorrow`);
}

export function requireBearer(req: Request, token: string | undefined): void {
  const got = req.headers.get("authorization") || "";
  if (!token || got !== `Bearer ${token}`) throw new HttpError(401, "not authorised");
}

export const NAME_RE = /^[a-z0-9][a-z0-9.+-]{1,62}$/;
export const VERSION_RE = /^[0-9A-Za-z][0-9A-Za-z.+~-]{0,63}$/;
export const RESERVED = /^(cherry($|-)|admin$|root$|system$|official$|debian$|apt$)/;
