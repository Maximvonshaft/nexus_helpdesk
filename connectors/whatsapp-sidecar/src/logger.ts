import pino from "pino";

export function createLogger(level = "info") {
  return pino({
    level,
    redact: {
      paths: [
        "qr",
        "*.qr",
        "auth",
        "*.auth",
        "body.auth",
        "body.qr",
        "body.phone_number",
        "body.phoneNumber",
        "headers.authorization",
        "jid",
        "*.jid",
        "node.attrs.jid",
        "node.attrs.participant",
        "node.username",
        "phone_number",
        "*.phone_number",
        "phoneNumber",
        "*.phoneNumber",
        "request.phone_number",
        "request.phoneNumber",
        "username",
        "*.username",
        "user.id"
      ],
      remove: true
    }
  });
}
