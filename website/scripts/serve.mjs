import { createServer } from "node:http";
import { readFile, realpath, stat } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { websiteRoot } from "./build.mjs";

const types = new Map([
  [".html", "text/html; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".webp", "image/webp"],
  [".ico", "image/x-icon"],
  [".txt", "text/plain; charset=utf-8"],
]);

export const securityHeaders = {
  "Content-Security-Policy":
    "default-src 'none'; style-src 'self'; img-src 'self'; script-src 'self'; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "X-Frame-Options": "DENY",
  "Cache-Control": "no-store",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
};

export async function startPreview({
  root = path.join(websiteRoot, "dist"),
  port = 4174,
} = {}) {
  const absoluteRoot = await realpath(root);
  const server = createServer(async (request, response) => {
    for (const [name, value] of Object.entries(securityHeaders))
      response.setHeader(name, value);
    const respond = (status, body) => {
      response.writeHead(status, {
        "Content-Type": "text/plain; charset=utf-8",
      });
      response.end(request.method === "HEAD" ? undefined : body);
    };
    if (!["GET", "HEAD"].includes(request.method)) {
      response.setHeader("Allow", "GET, HEAD");
      respond(405, "Method not allowed");
      return;
    }
    try {
      const pathname = decodeURIComponent(
        new URL(request.url, "http://localhost").pathname,
      );
      if (
        pathname.startsWith("//") ||
        pathname.includes("\\") ||
        pathname.includes("\0") ||
        pathname.split("/").includes("..")
      ) {
        respond(404, "Not found");
        return;
      }
      const relative = pathname.endsWith("/")
        ? `${pathname}index.html`
        : pathname;
      const candidate = path.resolve(absoluteRoot, `.${relative}`);
      const resolved = await realpath(candidate);
      if (!resolved.startsWith(`${absoluteRoot}${path.sep}`)) {
        respond(404, "Not found");
        return;
      }
      const info = await stat(resolved);
      if (info.isDirectory()) {
        response.writeHead(308, { Location: `${pathname}/` });
        response.end();
        return;
      }
      const type = types.get(path.extname(resolved));
      if (!type || !info.isFile()) {
        respond(404, "Not found");
        return;
      }
      const body = await readFile(resolved);
      response.writeHead(200, {
        "Content-Type": type,
        "Content-Length": body.length,
      });
      response.end(request.method === "HEAD" ? undefined : body);
    } catch (error) {
      const missing = ["ENOENT", "ENOTDIR"].includes(error.code);
      respond(
        error instanceof URIError ? 400 : missing ? 404 : 500,
        missing ? "Not found" : "Request failed",
      );
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
  return server;
}

if (
  process.argv[1] &&
  pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url
) {
  try {
    const args = process.argv.slice(2);
    if (args.length && (args.length !== 2 || args[0] !== "--port")) {
      throw new Error("Usage: node website/scripts/serve.mjs [--port 4174]");
    }
    const port = args.length ? Number(args[1]) : 4174;
    if (!Number.isInteger(port) || port < 1024 || port > 65535)
      throw new Error("Invalid preview port.");
    const server = await startPreview({ port });
    process.stdout.write(`Website preview: http://127.0.0.1:${port}\n`);
    for (const signal of ["SIGINT", "SIGTERM"])
      process.on(signal, () => server.close());
  } catch (error) {
    process.stderr.write(
      `${error.code === "ENOENT" ? "Build the website before starting its preview." : error.message}\n`,
    );
    process.exitCode = 1;
  }
}
