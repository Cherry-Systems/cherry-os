import { AwsClient } from "aws4fetch";
import { Env, HttpError, MB } from "./util";

/** Where package files live. Keys look like "p/<name>/<ver>/<n>". */
export interface Storage {
  put(key: string, data: ArrayBuffer): Promise<void>;
  get(key: string): Promise<ReadableStream | ArrayBuffer | null>;
  deletePrefix(prefix: string): Promise<void>;
}

// D1 rows are limited to 2 MB.
const D1_PART = 1_900_000;

class D1Storage implements Storage {
  constructor(private db: D1Database) {}

  async put(key: string, data: ArrayBuffer): Promise<void> {
    const stmts = [this.db.prepare("DELETE FROM blobs WHERE key = ?1").bind(key)];
    for (let off = 0, part = 0; off < data.byteLength || part === 0; off += D1_PART, part++) {
      stmts.push(this.db.prepare("INSERT INTO blobs (key, part, data) VALUES (?1, ?2, ?3)")
        .bind(key, part, data.slice(off, off + D1_PART)));
    }
    await this.db.batch(stmts);
  }

  async get(key: string): Promise<ArrayBuffer | null> {
    const { results } = await this.db.prepare("SELECT data FROM blobs WHERE key = ?1 ORDER BY part")
      .bind(key).all<{ data: ArrayBuffer | number[] }>();
    if (!results.length) return null;
    const parts = results.map((r) => (r.data instanceof ArrayBuffer ? new Uint8Array(r.data) : Uint8Array.from(r.data)));
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let off = 0;
    for (const p of parts) { out.set(p, off); off += p.length; }
    return out.buffer;
  }

  async deletePrefix(prefix: string): Promise<void> {
    await this.db.prepare("DELETE FROM blobs WHERE key >= ?1 AND key < ?2").bind(prefix, prefix + "\uffff").run();
  }
}

class R2Storage implements Storage {
  constructor(private bucket: R2Bucket) {}

  async put(key: string, data: ArrayBuffer): Promise<void> {
    await this.bucket.put(key, data);
  }

  async get(key: string): Promise<ReadableStream | null> {
    const obj = await this.bucket.get(key);
    return obj ? obj.body : null;
  }

  async deletePrefix(prefix: string): Promise<void> {
    let cursor: string | undefined;
    do {
      const list = await this.bucket.list({ prefix, cursor });
      if (list.objects.length) await this.bucket.delete(list.objects.map((o) => o.key));
      cursor = list.truncated ? list.cursor : undefined;
    } while (cursor);
  }
}

class S3Storage implements Storage {
  private aws: AwsClient;
  constructor(private endpoint: string, private bucket: string, keyId: string, secret: string) {
    this.aws = new AwsClient({ accessKeyId: keyId, secretAccessKey: secret, service: "s3" });
  }

  private url(key: string) {
    return `${this.endpoint.replace(/\/$/, "")}/${this.bucket}/${key.split("/").map(encodeURIComponent).join("/")}`;
  }

  async put(key: string, data: ArrayBuffer): Promise<void> {
    const res = await this.aws.fetch(this.url(key), { method: "PUT", body: data });
    if (!res.ok) throw new HttpError(502, `storage error ${res.status}`);
  }

  async get(key: string): Promise<ReadableStream | null> {
    const res = await this.aws.fetch(this.url(key));
    if (res.status === 404) return null;
    if (!res.ok) throw new HttpError(502, `storage error ${res.status}`);
    return res.body;
  }

  async deletePrefix(prefix: string): Promise<void> {
    const base = `${this.endpoint.replace(/\/$/, "")}/${this.bucket}`;
    for (;;) {
      const res = await this.aws.fetch(`${base}?list-type=2&prefix=${encodeURIComponent(prefix)}`);
      const keys = [...(await res.text()).matchAll(/<Key>([^<]+)<\/Key>/g)].map((m) => m[1]);
      if (!keys.length) return;
      await Promise.all(keys.map((k) => this.aws.fetch(this.url(k), { method: "DELETE" })));
    }
  }
}

export function storage(env: Env): Storage {
  switch (env.STORAGE) {
    case "r2":
      if (!env.BUCKET) throw new HttpError(500, "STORAGE=r2 but no BUCKET binding");
      return new R2Storage(env.BUCKET);
    case "s3":
      if (!env.S3_ENDPOINT || !env.S3_BUCKET || !env.S3_ACCESS_KEY_ID || !env.S3_SECRET_ACCESS_KEY) {
        throw new HttpError(500, "STORAGE=s3 needs S3_ENDPOINT, S3_BUCKET and the access key secrets");
      }
      return new S3Storage(env.S3_ENDPOINT, env.S3_BUCKET, env.S3_ACCESS_KEY_ID, env.S3_SECRET_ACCESS_KEY);
    default:
      return new D1Storage(env.DB);
  }
}

/** Tracks total stored bytes so the free tier can't be overrun. Returns false if the limit would be passed. */
export async function reserveBytes(env: Env, bytes: number): Promise<boolean> {
  const limit = Number(env.STORAGE_LIMIT_MB || "0") * MB;
  const row = limit > 0
    ? await env.DB.prepare("UPDATE usage SET bytes = bytes + ?1 WHERE id = 1 AND bytes + ?1 <= ?2 RETURNING bytes")
      .bind(bytes, limit).first()
    : await env.DB.prepare("UPDATE usage SET bytes = bytes + ?1 WHERE id = 1 RETURNING bytes").bind(bytes).first();
  return row !== null;
}

export async function releaseBytes(env: Env, bytes: number): Promise<void> {
  if (bytes > 0) await env.DB.prepare("UPDATE usage SET bytes = MAX(0, bytes - ?1) WHERE id = 1").bind(bytes).run();
}
