import { authenticate, keyIdFor, verifyEd25519, verifySelfSigned } from "./auth";
import { releaseBytes, reserveBytes, storage } from "./storage";
import {
  CHUNK_MAX, clientId, Env, hex, HttpError, json, MB, NAME_RE, now, rateLimit, readBody, readJson, requireBearer,
  RESERVED, VERSION_RE,
} from "./util";

/**
 * Cherry accounts for the Cherry Store. The store itself is the Cherry-Systems/cherry-store repository on
 * GitHub: its workflow checks, virus-scans and lists every package. Cherry Cloud is how people without a
 * GitHub account publish: it holds their upload only until that workflow has fetched it, and passes on
 * withdrawals and reports.
 */

const USERNAME_RE = /^[a-z0-9][a-z0-9_-]{2,31}$/;
const UPLOAD_RE = /^[0-9a-f]{32}$/;

/** The exact bytes a publisher signs for a package file. */
export const packageSigningMessage = (name: string, version: string, sha256: string) =>
  `cherry-package\n${name}\n${version}\n${sha256}`;

export async function signup(req: Request, env: Env): Promise<Response> {
  const { body, raw } = await readJson<{ username?: string; email?: string; public_key?: string }>(req);
  const username = (body.username || "").toLowerCase();
  if (!USERNAME_RE.test(username)) throw new HttpError(400, "username must be 3-32 letters, numbers, - or _");
  if (RESERVED.test(username)) throw new HttpError(400, "that username is reserved");
  if (!body.public_key) throw new HttpError(400, "public_key required");
  const keyId = await verifySelfSigned(req, body.public_key, raw);
  await rateLimit(env, "signup", await clientId(req, env), 5);
  const t = now();
  const user = await env.DB.prepare(
    "INSERT INTO users (username, email, created_at) VALUES (?1, ?2, ?3) ON CONFLICT(username) DO NOTHING RETURNING id",
  ).bind(username, (body.email || "").slice(0, 200) || null, t).first<{ id: number }>();
  if (!user) throw new HttpError(409, "that username is taken");
  await env.DB.prepare("INSERT INTO user_keys (key_id, user_id, public_key, label, created_at) VALUES (?1, ?2, ?3, 'first key', ?4)")
    .bind(keyId, user.id, body.public_key, t).run();
  return json({ username, key_id: keyId }, 201);
}

export async function whoami(req: Request, env: Env): Promise<Response> {
  const { raw } = await readJson(req);
  const caller = await authenticate(req, env, raw);
  return json({ username: caller.username, key_id: caller.keyId });
}

export async function addKey(req: Request, env: Env): Promise<Response> {
  const { body, raw } = await readJson<{ public_key?: string; label?: string }>(req);
  const caller = await authenticate(req, env, raw);
  if (!body.public_key) throw new HttpError(400, "public_key required");
  const keyId = await keyIdFor(body.public_key);
  await env.DB.prepare("INSERT INTO user_keys (key_id, user_id, public_key, label, created_at) VALUES (?1, ?2, ?3, ?4, ?5)")
    .bind(keyId, caller.userId, body.public_key, (body.label || "").slice(0, 60), now()).run();
  return json({ key_id: keyId }, 201);
}

export async function revokeKey(req: Request, env: Env, keyId: string): Promise<Response> {
  const { raw } = await readJson(req);
  const caller = await authenticate(req, env, raw);
  const res = await env.DB.prepare("UPDATE user_keys SET revoked = 1 WHERE key_id = ?1 AND user_id = ?2")
    .bind(keyId, caller.userId).run();
  if (!res.meta.changes) throw new HttpError(404, "no such key on your account");
  return json({ revoked: keyId });
}

export async function getUser(env: Env, username: string): Promise<Response> {
  const user = await env.DB.prepare("SELECT id, username, created_at, banned FROM users WHERE username = ?1")
    .bind(username).first<{ id: number; username: string; created_at: number; banned: number }>();
  if (!user) throw new HttpError(404, "no such user");
  const keys = await env.DB.prepare("SELECT key_id, public_key, label, created_at, revoked FROM user_keys WHERE user_id = ?1")
    .bind(user.id).all();
  return json({ username: user.username, created_at: user.created_at, suspended: !!user.banned, keys: keys.results });
}

// ---- GitHub ----

async function github(env: Env, path: string, body: unknown): Promise<Response> {
  if (!env.GITHUB_TOKEN) throw new HttpError(503, "the Cherry Store isn't connected to GitHub yet");
  const res = await fetch(`${env.GITHUB_API || "https://api.github.com"}/repos/${env.STORE_REPO}${path}`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: "application/vnd.github+json",
      "user-agent": "cherry-cloud",
      "x-github-api-version": "2022-11-28",
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    console.error("GitHub", path, res.status, await res.text());
    throw new HttpError(502, "couldn't reach the Cherry Store on GitHub; try again in a minute");
  }
  return res;
}

const dispatch = (env: Env, event: string, payload: Record<string, unknown>) =>
  github(env, "/dispatches", { event_type: event, client_payload: payload });

// ---- Publishing with a Cherry account ----

interface PackageRequest {
  name?: string;
  version?: string;
  sha256?: string;
  signature?: string;
  public_key?: string;
  [k: string]: unknown;
}

export async function startUpload(req: Request, env: Env): Promise<Response> {
  const { body, raw } = await readJson<{ request?: PackageRequest; size?: number; chunks?: number }>(req);
  const caller = await authenticate(req, env, raw);
  const r = body.request || {};
  const name = String(r.name || ""), version = String(r.version || "");
  if (!NAME_RE.test(name)) throw new HttpError(400, "package names are 2-63 lowercase letters, numbers, . + -");
  if (!VERSION_RE.test(version)) throw new HttpError(400, "invalid version");
  if (RESERVED.test(name)) throw new HttpError(403, "that name is reserved for Cherry OS itself");
  const size = Number(body.size), chunks = Number(body.chunks);
  if (!(size > 0) || size > Number(env.MAX_PACKAGE_MB) * MB) {
    throw new HttpError(413, `Cherry accounts can publish packages up to ${env.MAX_PACKAGE_MB} MB (use --github for bigger ones)`);
  }
  if (!(chunks >= 1) || chunks !== Math.ceil(size / CHUNK_MAX)) throw new HttpError(400, "chunk count doesn't match size");
  if (!/^[0-9a-f]{64}$/.test(r.sha256 || "") || !r.signature || !r.public_key) {
    throw new HttpError(400, "sha256, signature and public_key required");
  }
  const key = await env.DB.prepare("SELECT revoked FROM user_keys WHERE key_id = ?1 AND user_id = ?2")
    .bind(await keyIdFor(r.public_key), caller.userId).first<{ revoked: number }>();
  if (!key || key.revoked) throw new HttpError(400, "packages must be signed with one of your account's keys");
  if (!(await verifyEd25519(r.public_key, packageSigningMessage(name, version, r.sha256!), r.signature))) {
    throw new HttpError(400, "the package signature doesn't verify");
  }
  await rateLimit(env, "publish", String(caller.userId), 20);
  if (!(await reserveBytes(env, size))) throw new HttpError(507, "Cherry Cloud is full right now; try again later or use --github");
  const id = hex(crypto.getRandomValues(new Uint8Array(16)));
  const t = now();
  await env.DB.prepare(
    `INSERT INTO uploads (id, user_id, name, version, request, size, chunks, created_at, updated_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?8)`,
  ).bind(id, caller.userId, name, version, JSON.stringify(r).slice(0, 16 * 1024), size, chunks, t).run();
  return json({ upload: id, chunk_size: CHUNK_MAX }, 201);
}

async function ownUpload(req: Request, env: Env, id: string, raw: string | null) {
  const caller = await authenticate(req, env, raw);
  if (!UPLOAD_RE.test(id)) throw new HttpError(404, "no such upload");
  const row = await env.DB.prepare("SELECT * FROM uploads WHERE id = ?1").bind(id)
    .first<{ user_id: number; state: string; chunks: number; size: number; request: string }>();
  if (!row || row.user_id !== caller.userId) throw new HttpError(404, "no such upload");
  return { caller, row };
}

export async function putChunk(req: Request, env: Env, id: string, n: number): Promise<Response> {
  const { row } = await ownUpload(req, env, id, null);
  if (row.state !== "uploading") throw new HttpError(409, "this upload was already sent to the store");
  if (!(n >= 0 && n < row.chunks)) throw new HttpError(400, "chunk number out of range");
  await storage(env).put(`u/${id}/${n}`, await readBody(req, CHUNK_MAX));
  return json({ ok: true });
}

export async function finishUpload(req: Request, env: Env, id: string): Promise<Response> {
  const { raw } = await readJson(req);
  const { caller, row } = await ownUpload(req, env, id, raw);
  if (row.state !== "uploading") return json({ state: row.state });
  await env.DB.prepare("UPDATE uploads SET state = 'waiting', updated_at = ?2 WHERE id = ?1").bind(id, now()).run();
  try {
    await dispatch(env, "cherry-upload", { upload: id, username: caller.username, chunks: row.chunks, request: JSON.parse(row.request) });
  } catch (err) {
    await drop(env, id, row.size, "failed", "Couldn't hand it to the store; publish it again.");
    throw err;
  }
  return json({ state: "waiting" });
}

export async function uploadStatus(env: Env, id: string): Promise<Response> {
  if (!UPLOAD_RE.test(id)) throw new HttpError(404, "no such upload");
  const row = await env.DB.prepare("SELECT name, version, state, message FROM uploads WHERE id = ?1").bind(id).first();
  if (!row) throw new HttpError(404, "no such upload");
  return json(row);
}

/** Deletes an upload's file and records how it ended. */
async function drop(env: Env, id: string, size: number, state: string, message: string): Promise<void> {
  await storage(env).deletePrefix(`u/${id}/`);
  await releaseBytes(env, size);
  await env.DB.prepare("UPDATE uploads SET state = ?2, message = ?3, size = 0, updated_at = ?4 WHERE id = ?1")
    .bind(id, state, message.slice(0, 4000), now()).run();
}

// ---- Called by the store's GitHub workflow (Bearer STORE_TOKEN) ----

export async function handoffChunk(req: Request, env: Env, id: string, n: number): Promise<Response> {
  requireBearer(req, env.STORE_TOKEN);
  const row = await env.DB.prepare("SELECT chunks FROM uploads WHERE id = ?1 AND state = 'waiting'").bind(id).first<{ chunks: number }>();
  if (!row || !(n >= 0 && n < row.chunks)) throw new HttpError(404, "not found");
  const data = await storage(env).get(`u/${id}/${n}`);
  if (!data) throw new HttpError(404, "chunk missing");
  return new Response(data, { headers: { "content-type": "application/octet-stream" } });
}

export async function handoffDone(req: Request, env: Env, id: string): Promise<Response> {
  requireBearer(req, env.STORE_TOKEN);
  const { body } = await readJson<{ ok?: boolean; message?: string }>(req);
  const row = await env.DB.prepare("SELECT size FROM uploads WHERE id = ?1").bind(id).first<{ size: number }>();
  if (!row) throw new HttpError(404, "no such upload");
  await drop(env, id, row.size, body.ok ? "done" : "failed", String(body.message || ""));
  return json({ ok: true });
}

// ---- Withdrawing and reporting ----

export async function yank(req: Request, env: Env): Promise<Response> {
  const { body, raw } = await readJson<{ request?: { name?: string; version?: string; signature?: string } }>(req);
  const caller = await authenticate(req, env, raw);
  const r = body.request || {};
  if (!NAME_RE.test(r.name || "") || !VERSION_RE.test(r.version || "") || !r.signature) {
    throw new HttpError(400, "name, version and signature required");
  }
  await rateLimit(env, "yank", String(caller.userId), 20);
  await dispatch(env, "cherry-yank", { username: caller.username, request: { name: r.name, version: r.version, signature: r.signature } });
  return json({ message: `Asked the store to withdraw ${r.name} ${r.version}; it disappears within a few minutes.` });
}

export async function report(req: Request, env: Env): Promise<Response> {
  const { body } = await readJson<{ name?: string; reason?: string; details?: string }>(req);
  const reasons = ["malware", "illegal", "impersonation", "broken", "other"];
  if (!NAME_RE.test(body.name || "") || !reasons.includes(body.reason || "")) {
    throw new HttpError(400, `reason must be one of ${reasons.join(", ")}`);
  }
  await rateLimit(env, "report", await clientId(req, env), 10);
  const details = (body.details || "").slice(0, 4000).replace(/@/g, "@\u200b");
  await github(env, "/issues", {
    title: `Report: ${body.name} (${body.reason})`,
    body: `### Package name\n\n${body.name}\n\n### What's wrong with it?\n\n${body.reason}: ${details || "(no details)"}\n\n_Sent from Cherry OS through Cherry Cloud._`,
    labels: ["report"],
  });
  return json({ ok: true, message: "Thanks. Reports of malware and illegal content are checked and taken down if confirmed." }, 201);
}

/** Nightly: drops uploads the store never collected, finished upload records, old nonces and rate counters. */
export async function cleanup(env: Env): Promise<void> {
  const t = now();
  const stale = await env.DB.prepare(
    "SELECT id, size FROM uploads WHERE state IN ('uploading', 'waiting') AND updated_at < ?1 LIMIT 200",
  ).bind(t - 86400).all<{ id: string; size: number }>();
  for (const u of stale.results) await drop(env, u.id, u.size, "failed", "The upload expired before the store collected it.");
  const day = Math.floor(Date.now() / 86_400_000);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM uploads WHERE state IN ('done', 'failed') AND updated_at < ?1").bind(t - 7 * 86400),
    env.DB.prepare("DELETE FROM nonces WHERE created_at < ?1").bind(t - 900),
    // Counter keys end in ":<day number>"; keep today's and yesterday's.
    env.DB.prepare("DELETE FROM rate WHERE key NOT LIKE ('%:' || ?1) AND key NOT LIKE ('%:' || ?2)").bind(day, day - 1),
  ]);
}
