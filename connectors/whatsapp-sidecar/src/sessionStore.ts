import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";
import type { SessionState } from "./types.js";

const SAFE_ACCOUNT_ID = /^[a-zA-Z0-9._-]{1,160}$/;

export interface SessionSnapshot {
  state: SessionState;
  hasCreds: boolean;
  hasBackup: boolean;
  phoneNumber: string | null;
  jid: string | null;
  platform: string | null;
  accountSyncCounter: number | null;
  processedHistoryMessages: number | null;
}

export function assertSafeAccountId(accountId: string): string {
  const cleaned = accountId.trim();
  if (!SAFE_ACCOUNT_ID.test(cleaned)) {
    throw new Error("invalid_account_id");
  }
  return cleaned;
}

export class SessionStore {
  constructor(private readonly root: string) {}

  accountPath(accountId: string): string {
    const path = this.safeAccountPath(accountId);
    mkdirSync(path, { recursive: true, mode: 0o700 });
    return path;
  }

  backupCreds(accountId: string): boolean {
    const credsPath = this.credsPath(accountId);
    const raw = this.readValidJsonRaw(credsPath);
    if (!raw) return false;
    const backupPath = this.backupPath(accountId);
    const tempPath = `${backupPath}.${process.pid}.${Date.now()}.tmp`;
    writeFileSync(tempPath, raw, { mode: 0o600 });
    renameSync(tempPath, backupPath);
    return true;
  }

  inspectAccount(accountId: string): SessionSnapshot {
    const credsPath = this.credsPath(accountId);
    const backupPath = this.backupPath(accountId);
    const parsed = this.readValidJson(credsPath);
    const backup = this.readValidJson(backupPath);
    if (!parsed) {
      return {
        state: existsSync(credsPath) ? "corrupt" : "empty",
        hasCreds: existsSync(credsPath),
        hasBackup: Boolean(backup),
        phoneNumber: null,
        jid: null,
        platform: null,
        accountSyncCounter: null,
        processedHistoryMessages: null
      };
    }
    const jid = this.extractJid(parsed);
    const accountPresent = parsed.account && typeof parsed.account === "object";
    return {
      state: accountPresent && jid ? "linked" : jid ? "partial" : "empty",
      hasCreds: true,
      hasBackup: Boolean(backup),
      phoneNumber: this.extractPhoneNumber(jid),
      jid,
      platform: typeof parsed.platform === "string" ? parsed.platform : null,
      accountSyncCounter: typeof parsed.accountSyncCounter === "number" ? parsed.accountSyncCounter : null,
      processedHistoryMessages: Array.isArray(parsed.processedHistoryMessages) ? parsed.processedHistoryMessages.length : null
    };
  }

  restoreCredsBackupIfNeeded(accountId: string): boolean {
    const credsPath = this.credsPath(accountId);
    if (this.readValidJson(credsPath)) return false;
    const backupPath = this.backupPath(accountId);
    const raw = this.readValidJsonRaw(backupPath);
    if (!raw) return false;
    const tempPath = `${credsPath}.${process.pid}.${Date.now()}.tmp`;
    writeFileSync(tempPath, raw, { mode: 0o600 });
    renameSync(tempPath, credsPath);
    return true;
  }

  resetAccount(accountId: string): void {
    const path = this.safeAccountPath(accountId);
    rmSync(path, { recursive: true, force: true });
    mkdirSync(path, { recursive: true, mode: 0o700 });
  }

  private credsPath(accountId: string): string {
    return join(this.accountPath(accountId), "creds.json");
  }

  private backupPath(accountId: string): string {
    return join(this.accountPath(accountId), "creds.json.bak");
  }

  private readValidJson(filePath: string): Record<string, any> | null {
    const raw = this.readValidJsonRaw(filePath);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as Record<string, any>;
    } catch {
      return null;
    }
  }

  private readValidJsonRaw(filePath: string): string | null {
    try {
      const stat = statSync(filePath);
      if (!stat.isFile() || stat.size <= 1) return null;
      const raw = readFileSync(filePath, "utf8");
      JSON.parse(raw);
      return raw;
    } catch {
      return null;
    }
  }

  private extractJid(creds: Record<string, any>): string | null {
    const id = creds.me?.id;
    return typeof id === "string" && id.trim() ? id : null;
  }

  private extractPhoneNumber(jid: string | null): string | null {
    if (!jid) return null;
    const digits = jid.split("@")[0]?.split(":")[0]?.replace(/\D/g, "") || "";
    return digits ? `+${digits}` : null;
  }

  private safeAccountPath(accountId: string): string {
    const safe = assertSafeAccountId(accountId);
    const path = resolve(join(this.root, safe));
    const root = resolve(this.root);
    const accountRelativePath = relative(root, path);
    if (accountRelativePath.startsWith("..") || isAbsolute(accountRelativePath)) {
      throw new Error("invalid_account_id");
    }
    return path;
  }
}
