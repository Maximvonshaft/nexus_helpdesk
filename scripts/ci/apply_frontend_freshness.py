from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "webapp/src/features/support-console/SupportConsolePage.tsx"
E2E = ROOT / "webapp/e2e/smoke.spec.ts"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    console = CONSOLE.read_text(encoding="utf-8")
    old_header = '''        <div className="support-head-status" aria-label="实时状态">
          <Badge tone="default">{state.data?.open ?? 0} 个打开会话</Badge>
          <Badge tone="danger">{state.data?.requested_handoffs ?? 0} 个待人工</Badge>
          <Badge tone="default">{state.data?.my_handoffs ?? 0} 个我的接管</Badge>
          <span className="support-user">{session.data?.display_name || session.data?.username || '客服'}</span>
          <Button variant="ghost" onClick={handleLogout}>退出</Button>
        </div>
'''
    new_header = '''        <div className="support-head-status" aria-label="实时状态">
          {activeView === 'conversations' ? (
            <>
              <Badge tone="default">{state.data?.open ?? 0} 个打开会话</Badge>
              <Badge tone="danger">{state.data?.requested_handoffs ?? 0} 个待人工</Badge>
              <Badge tone="default">{state.data?.my_handoffs ?? 0} 个我的接管</Badge>
            </>
          ) : (
            <Badge tone="default">会话状态暂停刷新</Badge>
          )}
          <span className="support-user">{session.data?.display_name || session.data?.username || '客服'}</span>
          <Button variant="ghost" onClick={handleLogout}>退出</Button>
        </div>
'''
    console = replace_once(console, old_header, new_header, "freshness-aligned header")
    CONSOLE.write_text(console, encoding="utf-8")

    e2e = E2E.read_text(encoding="utf-8")
    old_knowledge = '''  await page.getByRole('button', { name: '知识' }).click()
  await expect(page.getByRole('button', { name: /Delivery status/ })).toBeVisible()
'''
    new_knowledge = '''  await page.getByRole('button', { name: '知识' }).click()
  await expect(page.getByRole('button', { name: /Delivery status/ })).toBeVisible()
  await expect(page.getByText('会话状态暂停刷新', { exact: true })).toBeVisible()
  await expect(page.getByText('1 个打开会话', { exact: true })).toHaveCount(0)
'''
    e2e = replace_once(e2e, old_knowledge, new_knowledge, "browser freshness evidence")
    E2E.write_text(e2e, encoding="utf-8")

    print("frontend visible freshness patch applied")


if __name__ == "__main__":
    main()
