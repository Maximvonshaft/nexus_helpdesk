import pino from "pino";

export function createLogger(level = "info") {
  return pino({
    level,
    redact: {
      paths: ["qr", "*.qr", "headers.authorization", "body.qr", "body.auth"],
      remove: true
    }
  });
}
