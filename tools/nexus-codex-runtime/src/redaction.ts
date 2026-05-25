import { createHash } from "node:crypto";

const JWT_RE = /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\b/g;
const BEARER_RE = /\bBearer\s+[A-Za-z0-9._-]{12,}/gi;
const SECRET_ASSIGNMENT_RE =
  /\b(accessToken|refreshToken|apiKey|authorization)(["']?\s*[:=]\s*["']?)[^"',\s}]+/gi;

export function fingerprintSecret(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex").slice(0, 16);
}

export function stableHash(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value), "utf8").digest("hex").slice(0, 16);
}

export function redact(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => redact(item));
  }
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (isSecretKey(key)) {
        out[key] = "<redacted>";
      } else {
        out[key] = redact(item);
      }
    }
    return out;
  }
  if (typeof value === "string") {
    return redactString(value);
  }
  return value;
}

export function redactString(value: string): string {
  return value
    .replace(BEARER_RE, "Bearer <redacted>")
    .replace(SECRET_ASSIGNMENT_RE, "$1$2<redacted>")
    .replace(JWT_RE, "<redacted-jwt>");
}

function isSecretKey(key: string): boolean {
  return [
    "accessToken",
    "access_token",
    "refreshToken",
    "refresh_token",
    "authorization",
    "apiKey",
    "api_key",
  ].includes(key);
}
