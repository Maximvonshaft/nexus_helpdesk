import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";

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
    const safe = assertSafeAccountId(accountId);
    const path = resolve(join(this.root, safe));
    const root = resolve(this.root);
    if (!path.startsWith(root)) {
      throw new Error("invalid_account_id");
    }
    mkdirSync(path, { recursive: true, mode: 0o700 });
    return path;
  }
}
