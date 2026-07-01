import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { Logger } from "pino";
import { AccountRegistry } from "./accountRegistry.js";
import { isAuthorized } from "./security.js";
import { assertSafeAccountId } from "./sessionStore.js";
import type { PairingCodeRequest, SendRequest, SidecarConfig } from "./types.js";

const MAX_BODY_BYTES = 64 * 1024;

interface RouteMatch {
  accountId: string;
  action: string;
}

function sendJson(res: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body)
  });
  res.end(body);
}

function matchAccountRoute(pathname: string): RouteMatch | null {
  const match = pathname.match(/^\/accounts\/([^/]+)\/([^/]+)$/);
  if (!match) return null;
  return { accountId: decodeURIComponent(match[1]), action: match[2] };
}

async function readJson(req: IncomingMessage): Promise<any> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > MAX_BODY_BYTES) {
      throw new Error("payload_too_large");
    }
    chunks.push(buffer);
  }
  const body = Buffer.concat(chunks).toString("utf8");
  return body ? JSON.parse(body) : {};
}

export function createSidecarServer(config: SidecarConfig, logger: Logger, registry = new AccountRegistry(config, logger)) {
  return createServer(async (req, res) => {
    try {
      const url = new URL(req.url || "/", "http://localhost");
      if (req.method === "GET" && url.pathname === "/healthz") {
        sendJson(res, 200, { status: "ok" });
        return;
      }
      if (req.method === "GET" && url.pathname === "/readyz") {
        sendJson(res, 200, { status: "ready", mode: config.mode });
        return;
      }
      if (!isAuthorized(req.headers.authorization, config.internalToken)) {
        sendJson(res, 401, { ok: false, error_code: "unauthorized" });
        return;
      }

      const route = matchAccountRoute(url.pathname);
      if (!route) {
        sendJson(res, 404, { ok: false, error_code: "not_found" });
        return;
      }
      const accountId = assertSafeAccountId(route.accountId);

      if (req.method === "POST" && route.action === "start") {
        sendJson(res, 200, await registry.start(accountId));
        return;
      }
      if (req.method === "POST" && route.action === "logout") {
        sendJson(res, 200, await registry.logout(accountId));
        return;
      }
      if (req.method === "POST" && route.action === "restart") {
        sendJson(res, 200, await registry.restart(accountId));
        return;
      }
      if (req.method === "GET" && route.action === "status") {
        sendJson(res, 200, await registry.status(accountId));
        return;
      }
      if (req.method === "GET" && route.action === "qr") {
        sendJson(res, 200, await registry.qr(accountId));
        return;
      }
      if (req.method === "POST" && route.action === "pairing-code") {
        const payload = await readJson(req) as PairingCodeRequest;
        const digits = String(payload.phone_number || "").replace(/\D/g, "");
        if (!/^\d{8,16}$/.test(digits)) {
          sendJson(res, 400, { ok: false, error_code: "invalid_phone_number" });
          return;
        }
        sendJson(res, 200, await registry.requestPairingCode(accountId, { phone_number: digits }));
        return;
      }
      if (req.method === "POST" && route.action === "send") {
        const payload = await readJson(req) as SendRequest;
        if (!payload.idempotency_key || !payload.body?.trim()) {
          sendJson(res, 400, { ok: false, error_code: "invalid_send_payload" });
          return;
        }
        sendJson(res, 200, await registry.send(accountId, payload));
        return;
      }
      sendJson(res, 405, { ok: false, error_code: "method_not_allowed" });
    } catch (error) {
      logger.warn({ error }, "request_failed");
      const message = error instanceof Error ? error.message : "internal_error";
      sendJson(res, message === "payload_too_large" ? 413 : 400, { ok: false, error_code: message });
    }
  });
}
