import { mkdirSync, rmSync } from "node:fs";
import { isAbsolute, join, relative, resolve } from "node:path";

const SAFE_ACCOUNT_ID = /^[a-zA-Z0-9._-]{1,160}$/;

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

  resetAccount(accountId: string): void {
    const path = this.safeAccountPath(accountId);
    rmSync(path, { recursive: true, force: true });
    mkdirSync(path, { recursive: true, mode: 0o700 });
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
