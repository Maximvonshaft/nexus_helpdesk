let started = false
let pending = false

const confirmedDangerousButtons = new WeakSet<HTMLButtonElement>()

const DANGEROUS_DRAWER_CONFIRMATIONS = [
  {
    label: '释放回队列',
    title: '确认释放回队列？',
    body: '释放后你将不能继续直接回复该会话，其他客服可以重新接入；AI 仍保持暂停。',
    confirmLabel: '释放回队列',
  },
  {
    label: '恢复 AI',
    title: '确认恢复 AI？',
    body: '恢复后，下一条客户消息可以重新触发 AI 自动回复。请确认人工处理已经完成。',
    confirmLabel: '恢复 AI',
  },
]

type DangerousDrawerConfirmation = (typeof DANGEROUS_DRAWER_CONFIRMATIONS)[number]

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

function drawerConfirmationFor(button: HTMLButtonElement): DangerousDrawerConfirmation | null {
  const label = normalizeText(button.textContent)
  return DANGEROUS_DRAWER_CONFIRMATIONS.find((item) => label.includes(item.label)) ?? null
}

function closeExistingDangerousActionConfirm() {
  document.querySelectorAll('.a11y-danger-confirm-overlay').forEach((element) => element.remove())
}

function showDangerousActionConfirm(button: HTMLButtonElement, confirmation: DangerousDrawerConfirmation) {
  closeExistingDangerousActionConfirm()

  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const titleId = `a11y-danger-confirm-title-${Date.now()}`
  const descriptionId = `a11y-danger-confirm-description-${Date.now()}`
  const overlay = document.createElement('div')
  overlay.className = 'a11y-danger-confirm-overlay'

  const dialog = document.createElement('div')
  dialog.className = 'a11y-danger-confirm-dialog'
  dialog.setAttribute('role', 'dialog')
  dialog.setAttribute('aria-modal', 'true')
  dialog.setAttribute('aria-labelledby', titleId)
  dialog.setAttribute('aria-describedby', descriptionId)

  const title = document.createElement('h2')
  title.id = titleId
  title.textContent = confirmation.title

  const body = document.createElement('p')
  body.id = descriptionId
  body.textContent = confirmation.body

  const actions = document.createElement('div')
  actions.className = 'a11y-danger-confirm-actions'

  const cancel = document.createElement('button')
  cancel.type = 'button'
  cancel.className = 'button secondary'
  cancel.textContent = '取消'

  const confirm = document.createElement('button')
  confirm.type = 'button'
  confirm.className = 'button danger'
  confirm.textContent = confirmation.confirmLabel

  function closeDialog(restoreFocus = true) {
    document.removeEventListener('keydown', onKeyDown, true)
    overlay.remove()
    if (restoreFocus) previousFocus?.focus?.()
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeDialog()
    }
  }

  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) closeDialog()
  })
  cancel.addEventListener('click', () => closeDialog())
  confirm.addEventListener('click', () => {
    confirmedDangerousButtons.add(button)
    closeDialog(false)
    button.click()
  })

  actions.append(cancel, confirm)
  dialog.append(title, body, actions)
  overlay.append(dialog)
  document.body.append(overlay)
  document.addEventListener('keydown', onKeyDown, true)
  cancel.focus()
}

function interceptDangerousDrawerActions(event: MouseEvent) {
  const target = event.target instanceof Element ? event.target : null
  const button = target?.closest('button')
  if (!(button instanceof HTMLButtonElement) || button.disabled) return
  if (!button.closest('.v5-context-drawer')) return

  if (confirmedDangerousButtons.has(button)) {
    confirmedDangerousButtons.delete(button)
    return
  }

  const confirmation = drawerConfirmationFor(button)
  if (!confirmation) return

  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation()
  showDangerousActionConfirm(button, confirmation)
}

export function repairA11ySemantics(root: ParentNode = document) {
  repairWebchatConversationLists(root)
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
  document.addEventListener('click', interceptDangerousDrawerActions, true)
  const observer = new MutationObserver(scheduleRepair)
  observer.observe(document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['class', 'role', 'aria-label', 'aria-selected', 'data-active'],
  })
}
