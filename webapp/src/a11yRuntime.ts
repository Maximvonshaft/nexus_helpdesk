let started = false
let pending = false

function setAttr(element: Element, name: string, value: string) {
  if (element.getAttribute(name) !== value) element.setAttribute(name, value)
}

function removeAttr(element: Element, name: string) {
  if (element.hasAttribute(name)) element.removeAttribute(name)
}

function normalizeText(value: string | null | undefined) {
  return String(value || '').replace(/\s+/g, ' ').trim()
}

function repairWebchatConversationLists(root: ParentNode = document) {
  root.querySelectorAll('[role="listbox"][aria-label="WebChat conversations"]').forEach((list) => {
    setAttr(list, 'role', 'list')
    setAttr(list, 'aria-label', 'WebChat conversations')

    list.querySelectorAll('[role="option"]').forEach((item) => {
      const selected = item.getAttribute('aria-selected') === 'true'
      removeAttr(item, 'role')
      removeAttr(item, 'aria-selected')
      setAttr(item, 'aria-pressed', selected ? 'true' : 'false')
      if (!item.getAttribute('aria-label')) {
        const label = normalizeText(item.textContent).slice(0, 140)
        setAttr(item, 'aria-label', label ? `打开 WebChat 会话：${label}` : '打开 WebChat 会话')
      }
    })
  })
}

function repairWebcallQueueFilters(root: ParentNode = document) {
  root.querySelectorAll('[role="tablist"][aria-label="WebCall Operational Queue tabs"]').forEach((group) => {
    setAttr(group, 'role', 'group')
    setAttr(group, 'aria-label', 'WebCall Operational Queue filters')

    group.querySelectorAll('button').forEach((button) => {
      const isActive = button.classList.contains('primary') || button.getAttribute('data-active') === 'true'
      setAttr(button, 'aria-pressed', isActive ? 'true' : 'false')
    })
  })
}

export function repairA11ySemantics(root: ParentNode = document) {
  repairWebchatConversationLists(root)
  repairWebcallQueueFilters(root)
}

function scheduleRepair() {
  if (pending) return
  pending = true
  window.requestAnimationFrame(() => {
    pending = false
    repairA11ySemantics()
  })
}

export function initA11yRuntimeRepair() {
  if (started || typeof window === 'undefined' || typeof document === 'undefined') return
  started = true
  scheduleRepair()
  const observer = new MutationObserver(scheduleRepair)
  observer.observe(document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['class', 'role', 'aria-label', 'aria-selected', 'data-active'],
  })
}
