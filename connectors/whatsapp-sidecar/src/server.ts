import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { Logger } from "pino";
import { AccountRegistry } from "./accountRegistry.js";
import { isAuthorized } from "./security.js";
import { assertSafeAccountId } from "./sessionStore.js";
import type {
  PairingCodeRequest,
  SendMediaRequest,
  SendRequest,
  SidecarConfig,
  WhatsAppMediaKind
} from "./types.js";

const MAX_JSON_BODY_BYTES = 64 * 1024;
const MEDIA_LIMITS: Record<WhatsAppMediaKind, number> = {
  image: 5 * 1024 * 1024,
  audio: 16 * 1024 * 1024,
  video: 16 * 1024 * 1024,
  document: 100 * 1024 * 1024,
  sticker: 500 * 1024
};

type PublicErrorCode =
  | "payload_too_large"
  | "whatsapp_connection_owner_busy"
  | "invalid_generation"
  | "invalid_account_id"
  | "invalid_phone_number"
  | "invalid_send_payload"
  | "invalid_media_payload"
  | "object_payload_required"
  | "internal_error";

interface RouteMatch {
  accountId: string;
  action: string;
}

function sendJson(res: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store"
  });
  res.end(body);
}

function matchAccountRoute(pathname: string): RouteMatch | null {
  const match = pathname.match(/^\/accounts\/([^/]+)\/([^/]+)$/);
  if (!match) return null;
  return { accountId: decodeURIComponent(match[1]), action: match[2] };
}

async function readBody(req: IncomingMessage, maxBytes: number): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > maxBytes) throw new Error("payload_too_large");
    chunks.push(buffer);
  }
  return Buffer.concat(chunks);
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const body = (await readBody(req, MAX_JSON_BODY_BYTES)).toString("utf8");
  if (!body) return {};
  const parsed = JSON.parse(body);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("object_payload_required");
  }
  return parsed as Record<string, unknown>;
}

function optionalGeneration(payload: Record<string, unknown>): number | undefined {
  if (payload.generation === undefined || payload.generation === null) return undefined;
  const generation = Number(payload.generation);
  if (!Number.isInteger(generation) || generation < 0) {
    throw new Error("invalid_generation");
  }
  return generation;
}

function publicErrorCode(error: unknown): PublicErrorCode {
  const message = error instanceof Error ? error.message : "";
  switch (message) {
    case "payload_too_large":
      return "payload_too_large";
    case "whatsapp_connection_owner_busy":
      return "whatsapp_connection_owner_busy";
    case "invalid_generation":
      return "invalid_generation";
    case "invalid_account_id":
      return "invalid_account_id";
    case "invalid_phone_number":
      return "invalid_phone_number";
    case "invalid_send_payload":
      return "invalid_send_payload";
    case "invalid_media_payload":
      return "invalid_media_payload";
    case "object_payload_required":
      return "object_payload_required";
    default:
      return "internal_error";
  }
}

function errorStatus(code: PublicErrorCode): number {
  if (code === "payload_too_large") return 413;
  if (code === "whatsapp_connection_owner_busy") return 409;
  if (code === "internal_error") return 500;
  return 400;
}

function header(req: IncomingMessage, name: string): string {
  const value = req.headers[name.toLowerCase()];
  return Array.isArray(value) ? value[0] || "" : String(value || "");
}

function mediaKind(value: string): WhatsAppMediaKind {
  if (
    value === "image" ||
    value === "video" ||
    value === "audio" ||
    value === "document" ||
    value === "sticker"
  ) {
    return value;
  }
  throw new Error("invalid_media_payload");
}

function optionalPositiveInteger(req: IncomingMessage, name: string): number | undefined {
  const value = header(req, name).trim();
  if (!value) return undefined;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    throw new Error("invalid_media_payload");
  }
  return parsed;
}

function optionalNonnegativeInteger(req: IncomingMessage, name: string): number | undefined {
  const value = header(req, name).trim();
  if (!value) return undefined;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0) {
    throw new Error("invalid_media_payload");
  }
  return parsed;
}

export function createSidecarServer(
  config: SidecarConfig,
  logger: Logger,
  registry = new AccountRegistry(config, logger)
) {
  return createServer(async (req, res) => {
    try {
      const url = new URL(req.url || "/", "http://localhost");
      if (req.method === "GET" && url.pathname === "/healthz") {
        sendJson(res, 200, { status: "ok" });
        return;
      }
      if (req.method === "GET" && url.pathname === "/readyz") {
        sendJson(res, 200, {
          status: "ready",
          mode: config.mode,
          desired_accounts: registry.desiredAccountCount(),
          pending_callbacks: registry.backend.pendingCallbacks()
        });
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
        const payload = await readJson(req);
        sendJson(res, 200, await registry.start(accountId, optionalGeneration(payload)));
        return;
      }
      if (req.method === "POST" && route.action === "stop") {
        sendJson(res, 200, await registry.stop(accountId));
        return;
      }
      if (req.method === "POST" && route.action === "logout") {
        sendJson(res, 200, await registry.logout(accountId));
        return;
      }
      if (req.method === "POST" && route.action === "restart") {
        const payload = await readJson(req);
        sendJson(res, 200, await registry.restart(accountId, optionalGeneration(payload)));
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
        const payload = await readJson(req) as unknown as PairingCodeRequest;
        const digits = String(payload.phone_number || "").replace(/\D/g, "");
        if (!/^\d{8,16}$/.test(digits)) {
          sendJson(res, 400, { ok: false, error_code: "invalid_phone_number" });
          return;
        }
        sendJson(res, 200, await registry.requestPairingCode(accountId, { phone_number: digits }));
        return;
      }
      if (req.method === "POST" && route.action === "send") {
        const payload = await readJson(req) as unknown as SendRequest;
        if (!payload.idempotency_key || !payload.body?.trim()) {
          sendJson(res, 400, { ok: false, error_code: "invalid_send_payload" });
          return;
        }
        sendJson(res, 200, await registry.send(accountId, payload));
        return;
      }
      if (req.method === "POST" && route.action === "send-media") {
        const kind = mediaKind(header(req, "x-nexus-media-kind").trim().toLowerCase());
        const idempotencyKey = header(req, "x-nexus-idempotency-key").trim();
        const mediaType = header(req, "x-nexus-media-type").split(";", 1)[0].trim().toLowerCase();
        if (!idempotencyKey || !mediaType) {
          throw new Error("invalid_media_payload");
        }
        const content = await readBody(req, MEDIA_LIMITS[kind]);
        if (!content.byteLength) throw new Error("invalid_media_payload");
        const metadata: Record<string, number> = {};
        const outboundMessageId = optionalPositiveInteger(req, "x-nexus-outbound-message-id");
        const ticketId = optionalPositiveInteger(req, "x-nexus-ticket-id");
        const connectionId = optionalPositiveInteger(req, "x-nexus-connection-id");
        const outboundPartId = optionalPositiveInteger(req, "x-nexus-outbound-part-id");
        const sequence = optionalNonnegativeInteger(req, "x-nexus-sequence");
        if (outboundMessageId !== undefined) metadata.outbound_message_id = outboundMessageId;
        if (ticketId !== undefined) metadata.ticket_id = ticketId;
        if (connectionId !== undefined) metadata.connection_id = connectionId;
        if (outboundPartId !== undefined) metadata.outbound_part_id = outboundPartId;
        if (sequence !== undefined) metadata.sequence = sequence;
        const request: SendMediaRequest = {
          idempotency_key: idempotencyKey,
          target: header(req, "x-nexus-target").trim() || null,
          chat_jid: header(req, "x-nexus-chat-jid").trim() || null,
          media_kind: kind,
          media_type: mediaType,
          filename: decodeURIComponent(header(req, "x-nexus-media-filename")).slice(0, 255) || null,
          caption: decodeURIComponent(header(req, "x-nexus-media-caption")).slice(0, 1024) || null,
          metadata
        };
        sendJson(res, 200, await registry.sendMedia(accountId, request, content));
        return;
      }
      sendJson(res, 405, { ok: false, error_code: "method_not_allowed" });
    } catch (error) {
      const code = publicErrorCode(error);
      logger.warn(
        {
          error_code: code,
          error_name: error instanceof Error ? error.name : "UnknownError"
        },
        "request_failed"
      );
      sendJson(res, errorStatus(code), { ok: false, error_code: code });
    }
  });
}
