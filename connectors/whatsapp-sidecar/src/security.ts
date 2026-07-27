import { createHmac, timingSafeEqual } from "node:crypto";

export function isAuthorized(authHeader: string | undefined, token: string): boolean {
  if (!authHeader?.startsWith("Bearer ")) return false;
  const provided = Buffer.from(authHeader.slice("Bearer ".length));
  const expected = Buffer.from(token);
  return provided.length === expected.length && timingSafeEqual(provided, expected);
}

export function connectorSignature(
  secret: string,
  timestamp: string,
  rawBody: string | Buffer
): string {
  const hmac = createHmac("sha256", secret);
  hmac.update(timestamp, "utf8");
  hmac.update(".", "utf8");
  hmac.update(rawBody);
  return hmac.digest("hex");
}

export function connectorHeaders(options: {
  accountId: string;
  connectorKey: string;
  hmacSecret: string;
  rawBody: string;
  timestamp?: string;
}): Record<string, string> {
  const timestamp = options.timestamp || new Date().toISOString();
  return {
    "content-type": "application/json",
    "x-nexus-connector-key": options.connectorKey,
    "x-nexus-account-id": options.accountId,
    "x-nexus-timestamp": timestamp,
    "x-nexus-signature": connectorSignature(options.hmacSecret, timestamp, options.rawBody)
  };
}

export function connectorBinaryHeaders(options: {
  accountId: string;
  connectorKey: string;
  hmacSecret: string;
  body: Buffer;
  messageId: string;
  mediaKind: string;
  mediaType: string;
  filename?: string | null;
  sha256: string;
  timestamp?: string;
}): Record<string, string> {
  const timestamp = options.timestamp || new Date().toISOString();
  const headers: Record<string, string> = {
    "content-type": options.mediaType,
    "content-length": String(options.body.byteLength),
    "x-nexus-connector-key": options.connectorKey,
    "x-nexus-account-id": options.accountId,
    "x-nexus-timestamp": timestamp,
    "x-nexus-signature": connectorSignature(options.hmacSecret, timestamp, options.body),
    "x-nexus-message-id": options.messageId,
    "x-nexus-media-kind": options.mediaKind,
    "x-nexus-media-type": options.mediaType,
    "x-nexus-media-sha256": options.sha256
  };
  if (options.filename) {
    headers["x-nexus-media-filename"] = encodeURIComponent(options.filename.slice(0, 255));
  }
  return headers;
}
