import { createHmac, timingSafeEqual } from "node:crypto";

export function isAuthorized(authHeader: string | undefined, token: string): boolean {
  if (!authHeader?.startsWith("Bearer ")) return false;
  const provided = Buffer.from(authHeader.slice("Bearer ".length));
  const expected = Buffer.from(token);
  return provided.length === expected.length && timingSafeEqual(provided, expected);
}

export function connectorSignature(secret: string, timestamp: string, rawBody: string): string {
  return createHmac("sha256", secret).update(`${timestamp}.${rawBody}`).digest("hex");
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
