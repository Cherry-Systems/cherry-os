import { downloadIso } from "./download";
import * as pk from "./packages";
import { Env, HttpError, json } from "./util";

type Handler = (req: Request, env: Env, params: string[], url: URL) => Promise<Response>;

const routes: [string, RegExp, Handler][] = [
  ["GET", /^\/api\/health$/, async (_r, env) => json({
    ok: true, store: env.STORE_REPO, github: !!env.GITHUB_TOKEN, max_package_mb: Number(env.MAX_PACKAGE_MB),
  })],

  // Accounts
  ["POST", /^\/api\/accounts$/, (r, e) => pk.signup(r, e)],
  ["POST", /^\/api\/accounts\/whoami$/, (r, e) => pk.whoami(r, e)],
  ["POST", /^\/api\/accounts\/keys$/, (r, e) => pk.addKey(r, e)],
  ["DELETE", /^\/api\/accounts\/keys\/([0-9a-f]{16})$/, (r, e, [k]) => pk.revokeKey(r, e, k)],
  ["GET", /^\/api\/users\/([^/]+)$/, (_r, e, [u]) => pk.getUser(e, u)],

  // Publishing with a Cherry account: held here until the store's GitHub workflow collects it
  ["POST", /^\/api\/uploads$/, (r, e) => pk.startUpload(r, e)],
  ["PUT", /^\/api\/uploads\/([0-9a-f]{32})\/chunks\/(\d+)$/, (r, e, [id, c]) => pk.putChunk(r, e, id, Number(c))],
  ["POST", /^\/api\/uploads\/([0-9a-f]{32})\/finish$/, (r, e, [id]) => pk.finishUpload(r, e, id)],
  ["GET", /^\/api\/uploads\/([0-9a-f]{32})$/, (_r, e, [id]) => pk.uploadStatus(e, id)],
  ["GET", /^\/api\/handoff\/([0-9a-f]{32})\/chunks\/(\d+)$/, (r, e, [id, c]) => pk.handoffChunk(r, e, id, Number(c))],
  ["POST", /^\/api\/handoff\/([0-9a-f]{32})\/done$/, (r, e, [id]) => pk.handoffDone(r, e, id)],
  ["POST", /^\/api\/yank$/, (r, e) => pk.yank(r, e)],
  ["POST", /^\/api\/reports$/, (r, e) => pk.report(r, e)],
];

type Index = { packages?: Record<string, { latest?: string | null; versions: Record<string, { url: string }> }> };

// The newest Cherry Store app for other Debian-based systems, published through the store itself.
async function downloadClient(env: Env): Promise<Response> {
  const res = await fetch(`https://raw.githubusercontent.com/${env.STORE_REPO}/main/index.json`, {
    cf: { cacheTtl: 300, cacheEverything: true },
  });
  if (!res.ok) return new Response("Can't reach the Cherry Store right now. Try again in a minute.\n", { status: 502 });
  const pkg = ((await res.json()) as Index).packages?.["cherry-store-client"];
  const url = pkg?.latest ? pkg.versions[pkg.latest]?.url : undefined;
  if (!url?.startsWith("https://github.com/")) {
    return new Response("The Cherry Store app isn't available yet.\n", { status: 404 });
  }
  return Response.redirect(url, 302);
}

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, PUT, DELETE, OPTIONS",
  "access-control-allow-headers": "content-type, authorization",
};

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname === "/download/cherry-store.deb" && (req.method === "GET" || req.method === "HEAD")) {
      return downloadClient(env);
    }
    if (url.pathname === "/download/cherry-os.iso" && (req.method === "GET" || req.method === "HEAD")) {
      return downloadIso(req, ctx);
    }
    if (!url.pathname.startsWith("/api/")) return env.ASSETS.fetch(req);
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
    try {
      for (const [method, re, handler] of routes) {
        const m = url.pathname.match(re);
        if (m && method === req.method) {
          const res = await handler(req, env, m.slice(1).map(decodeURIComponent), url);
          const out = new Response(res.body, res);
          for (const [k, v] of Object.entries(CORS)) out.headers.set(k, v);
          return out;
        }
      }
      throw new HttpError(404, "no such endpoint");
    } catch (err) {
      if (err instanceof HttpError) return json({ error: err.message }, err.status, CORS);
      console.error(err);
      return json({ error: "internal error" }, 500, CORS);
    }
  },

  async scheduled(_event: ScheduledController, env: Env): Promise<void> {
    await pk.cleanup(env);
  },
} satisfies ExportedHandler<Env>;
