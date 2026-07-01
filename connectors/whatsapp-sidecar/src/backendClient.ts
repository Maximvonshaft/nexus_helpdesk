import type { Logger } from "pino";
import { connectorHeaders } from "./security.js";
import type { NormalizedInboundMessage, SidecarConfig } from "./types.js";

function callbackErrorInfo(error: unknown): { error_code: string; error_message: string; status_code?: number } {
  const errorMessage = error instanceof Error ? error.message : String(error);
  const statusMatch = /^backend_callback_failed:(\d{3})$/.exec(errorMessage);
  if (statusMatch) {
    return {
      error_code: "backend_callback_http_error",
      error_message: errorMessage,
      status_code: Number.parseInt(statusMatch[1], 10)
    };
  }
  const errorName = error instanceof Error ? error.name : "";
  return {
    error_code: errorName === "AbortError" ? "backend_callback_timeout" : "backend_callback_transport_error",
    error_message: errorMessage
  };
}

export class BackendClient {
  constructor(
    private readonly config: SidecarConfig,
    private readonly logger: Logger
  ) {}

  async postInbound(message: NormalizedInboundMessage): Promise<void> {
    await this.post(`/api/integrations/whatsapp/native/inbound`, message.account_id, message);
  }

  async postStatus(accountId: string, payload: unknown): Promise<void> {
    await this.post(`/api/integrations/whatsapp/native/status`, accountId, payload);
  }

  async postDelivery(accountId: string, payload: unknown): Promise<void> {
    await this.post(`/api/integrations/whatsapp/native/delivery`, accountId, payload);
  }

  private async post(path: string, accountId: string, payload: unknown): Promise<void> {
    const rawBody = JSON.stringify(payload);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.config.callbackTimeoutMs);
    try {
      const response = await fetch(`${this.config.backendUrl}${path}`, {
        method: "POST",
        headers: connectorHeaders({
          accountId,
          connectorKey: this.config.connectorKey,
          hmacSecret: this.config.connectorHmacSecret,
          rawBody
        }),
        body: rawBody,
        signal: controller.signal
      });
      if (!response.ok) {
        throw new Error(`backend_callback_failed:${response.status}`);
      }
    } catch (error) {
      this.logger.warn(
        {
          account_id: accountId,
          path,
          ...callbackErrorInfo(error),
          error
        },
        "backend_callback_failed"
      );
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }
}
