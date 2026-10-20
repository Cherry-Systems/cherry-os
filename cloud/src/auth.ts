import { b64decode, Env, HttpError, now, sha256hex } from "./util";

/**
 * Publishers have no passwords. `cherry signup` makes an Ed25519 key pair on the user's computer and
 * every write request carries:
 *   Authorization: Cherry key=<key_id>,ts=<unix seconds>,sig=<base64 signature>
 * where the signature covers "METHOD\nPATH\nTS\nBODY_SHA256" (BODY_SHA256 is "UNSIGNED-PAYLOAD" for
 * chunk uploads; their integrity is covered by the signed package hash instead).
 */
export interface Caller {
  userId: number;
  username: string;
  keyId: string;
}

export const keyIdFor = async (publicKeyB64: string) => (await sha256hex(b64decode(publicKeyB64))).slice(0, 16);

export async function verifyEd25519(publicKeyB64: string, message: string | Uint8Array, sigB64: string): Promise<boolean> {
  try {
    const key = await crypto.subtle.importKey("raw", b64decode(publicKeyB64), { name: "Ed25519" }, false, ["verify"]);
    const data = typeof message === "string" ? new TextEncoder().encode(message) : message;
    return await crypto.subtle.verify({ name: "Ed25519" }, key, b64decode(sigB64), data);
  } catch {
    return false;
  }
}

export function parseAuth(req: Request): { key: string; ts: number; sig: string } | null {
  const h = req.headers.get("authorization") || "";
  if (!h.startsWith("Cherry ")) return null;
  const parts = Object.fromEntries(h.slice(7).split(",").map((p) => {
    const i = p.indexOf("=");
    return [p.slice(0, i).trim(), p.slice(i + 1).trim()];
  }));
  if (!parts.key || !parts.ts || !parts.sig) return null;
  return { key: parts.key, ts: Number(parts.ts), sig: parts.sig };
}

export async function signedMessage(req: Request, bodyHash: string, ts: number): Promise<string> {
  const url = new URL(req.url);
  return `${req.method}\n${url.pathname}\n${ts}\n${bodyHash}`;
}

/** Checks a signed request against a key that is already registered. */
export async function authenticate(req: Request, env: Env, rawBody: string | null): Promise<Caller> {
  const auth = parseAuth(req);
  if (!auth) throw new HttpError(401, "sign in first: run 'cherry signup' or 'cherry login'");
  if (Math.abs(now() - auth.ts) > 300) throw new HttpError(401, "request too old; check your computer's clock");
  const row = await env.DB.prepare(
    `SELECT k.public_key, k.revoked, u.id, u.username, u.banned FROM user_keys k JOIN users u ON u.id = k.user_id
     WHERE k.key_id = ?1`,
  ).bind(auth.key).first<{ public_key: string; revoked: number; id: number; username: string; banned: number }>();
  if (!row || row.revoked) throw new HttpError(401, "unknown or revoked key");
  if (row.banned) throw new HttpError(403, "this account has been suspended");
  const bodyHash = rawBody === null ? "UNSIGNED-PAYLOAD" : await sha256hex(rawBody);
  if (!(await verifyEd25519(row.public_key, await signedMessage(req, bodyHash, auth.ts), auth.sig))) {
    throw new HttpError(401, "bad signature");
  }
  const fresh = await env.DB.prepare("INSERT OR IGNORE INTO nonces (sig, created_at) VALUES (?1, ?2)")
    .bind(auth.sig, now()).run();
  if (!fresh.meta.changes) throw new HttpError(401, "request replayed");
  return { userId: row.id, username: row.username, keyId: auth.key };
}

/** For sign-up: the request must be signed by the key being registered (proves the caller holds it). */
export async function verifySelfSigned(req: Request, publicKeyB64: string, rawBody: string): Promise<string> {
  const auth = parseAuth(req);
  if (!auth) throw new HttpError(401, "missing signature");
  if (Math.abs(now() - auth.ts) > 300) throw new HttpError(401, "request too old; check your computer's clock");
  const keyId = await keyIdFor(publicKeyB64);
  if (auth.key !== keyId) throw new HttpError(400, "key id does not match public key");
  const ok = await verifyEd25519(publicKeyB64, await signedMessage(req, await sha256hex(rawBody), auth.ts), auth.sig);
  if (!ok) throw new HttpError(401, "bad signature");
  return keyId;
}
