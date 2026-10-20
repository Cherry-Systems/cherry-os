// The Cherry OS ISO as one download. GitHub allows at most 2 GB per release file, so the release holds the ISO in
// pieces plus a manifest listing them in order; this streams the pieces back to back, so visitors get the whole ISO.
// Ranges are supported so browsers can pause and resume.

const OS_REPO = "Cherry-Systems/cherry-os";

type Manifest = { tag: string; file: string; sha256: string; size: number; parts: { name: string; size: number }[] };

async function manifest(): Promise<Manifest | null> {
  const res = await fetch(`https://github.com/${OS_REPO}/releases/latest/download/cherry-os-download.json`, {
    cf: { cacheTtl: 300, cacheEverything: true },
  });
  if (!res.ok) return null;
  const m = (await res.json()) as Manifest;
  if (!m.parts?.length || m.parts.reduce((n, p) => n + p.size, 0) !== m.size) return null;
  return m;
}

// The one range asked for, as inclusive [start, end]; null for the whole file, "bad" if it's outside the file.
export function parseRange(header: string | null, size: number): [number, number] | null | "bad" {
  const m = header?.match(/^bytes=(\d*)-(\d*)$/);
  if (!m || (m[1] === "" && m[2] === "")) return null;
  let start: number, end: number;
  if (m[1] === "") {
    start = Math.max(0, size - Number(m[2]));
    end = size - 1;
  } else {
    start = Number(m[1]);
    end = m[2] === "" ? size - 1 : Math.min(Number(m[2]), size - 1);
  }
  return start > end || start >= size ? "bad" : [start, end];
}

// Which bytes of which pieces make up [start, end] of the whole file.
export function pieces(parts: Manifest["parts"], start: number, end: number) {
  const out: { name: string; from: number; to: number }[] = [];
  let offset = 0;
  for (const p of parts) {
    const first = offset, last = offset + p.size - 1;
    offset += p.size;
    if (last < start || first > end) continue;
    out.push({ name: p.name, from: Math.max(start, first) - first, to: Math.min(end, last) - first });
  }
  return out;
}

export async function downloadIso(req: Request, ctx: ExecutionContext): Promise<Response> {
  const m = await manifest();
  if (!m) return new Response("Can't reach the Cherry OS download right now. Try again in a minute.\n", { status: 502 });

  const etag = `"${m.sha256}"`;
  const ifRange = req.headers.get("if-range");
  const range = ifRange && ifRange !== etag ? null : parseRange(req.headers.get("range"), m.size);
  if (range === "bad") {
    return new Response(null, { status: 416, headers: { "content-range": `bytes */${m.size}` } });
  }
  const [start, end] = range ?? [0, m.size - 1];
  const headers: Record<string, string> = {
    "content-type": "application/x-iso9660-image",
    "content-disposition": `attachment; filename="${m.file}"`,
    "content-length": String(end - start + 1),
    "accept-ranges": "bytes",
    etag,
  };
  if (range) headers["content-range"] = `bytes ${start}-${end}/${m.size}`;
  const status = range ? 206 : 200;
  if (req.method === "HEAD") return new Response(null, { status, headers });

  const fetchPiece = async (p: { name: string; from: number; to: number }) => {
    const res = await fetch(`https://github.com/${OS_REPO}/releases/download/${m.tag}/${p.name}`, {
      headers: { range: `bytes=${p.from}-${p.to}` },
    });
    if (res.status !== 206 || !res.body) throw new Error(`${p.name}: GitHub answered ${res.status}`);
    return res.body;
  };

  const todo = pieces(m.parts, start, end);
  let first: ReadableStream;
  try {
    first = await fetchPiece(todo[0]);
  } catch (err) {
    console.error(err);
    return new Response("Can't reach the Cherry OS download right now. Try again in a minute.\n", { status: 502 });
  }

  const { readable, writable } = new FixedLengthStream(end - start + 1);
  ctx.waitUntil((async () => {
    try {
      await first.pipeTo(writable, { preventClose: true });
      for (const p of todo.slice(1)) await (await fetchPiece(p)).pipeTo(writable, { preventClose: true });
      await writable.close();
    } catch (err) {
      console.error(err);
      await writable.abort(err).catch(() => {});
    }
  })());
  return new Response(readable, { status, headers });
}
