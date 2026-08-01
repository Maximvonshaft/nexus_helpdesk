import { UI_LOCALE_STORAGE_KEY } from './localeAuthority'
import type { UiLocale } from './localeAuthority'

const copy: Record<Exclude<UiLocale, 'zh-CN'>, {
  title: string
  detail: string
  retry: string
  chinese: string
}> = {
  en: {
    title: 'The interface language could not be loaded',
    detail: 'Nexus did not start because the complete English catalog is unavailable. No customer or operational data has been changed.',
    retry: 'Try again',
    chinese: 'Use Simplified Chinese',
  },
  de: {
    title: 'Die Oberflächensprache konnte nicht geladen werden',
    detail: 'Nexus wurde nicht gestartet, weil der vollständige deutsche Katalog nicht verfügbar ist. Kunden- und Betriebsdaten wurden nicht geändert.',
    retry: 'Erneut versuchen',
    chinese: 'Vereinfachtes Chinesisch verwenden',
  },
}

function button(label: string, primary: boolean, onClick: () => void) {
  const element = document.createElement('button')
  element.type = 'button'
  element.textContent = label
  element.style.minHeight = '44px'
  element.style.padding = '10px 18px'
  element.style.borderRadius = '8px'
  element.style.border = primary ? '1px solid #0b57d0' : '1px solid #8a94a6'
  element.style.background = primary ? '#0b57d0' : '#ffffff'
  element.style.color = primary ? '#ffffff' : '#182230'
  element.style.font = '600 15px/1.4 system-ui, sans-serif'
  element.style.cursor = 'pointer'
  element.addEventListener('click', onClick)
  return element
}

export function renderCatalogLoadFailure(locale: Exclude<UiLocale, 'zh-CN'>) {
  const content = copy[locale]
  document.title = content.title
  document.body.replaceChildren()
  document.body.style.margin = '0'
  document.body.style.background = '#f5f7fa'
  document.body.style.color = '#182230'

  const main = document.createElement('main')
  main.setAttribute('role', 'main')
  main.style.minHeight = '100dvh'
  main.style.display = 'grid'
  main.style.placeItems = 'center'
  main.style.padding = '24px'

  const panel = document.createElement('section')
  panel.style.width = 'min(100%, 560px)'
  panel.style.padding = '28px'
  panel.style.border = '1px solid #d0d5dd'
  panel.style.borderRadius = '12px'
  panel.style.background = '#ffffff'
  panel.style.boxShadow = '0 8px 24px rgba(16, 24, 40, 0.08)'

  const heading = document.createElement('h1')
  heading.textContent = content.title
  heading.style.margin = '0'
  heading.style.font = '700 24px/1.3 system-ui, sans-serif'

  const detail = document.createElement('p')
  detail.textContent = content.detail
  detail.style.margin = '14px 0 22px'
  detail.style.font = '400 16px/1.6 system-ui, sans-serif'

  const actions = document.createElement('div')
  actions.style.display = 'flex'
  actions.style.flexWrap = 'wrap'
  actions.style.gap = '12px'
  actions.append(
    button(content.retry, true, () => window.location.reload()),
    button(content.chinese, false, () => {
      try {
        window.localStorage.setItem(UI_LOCALE_STORAGE_KEY, 'zh-CN')
      } catch {
        // A full navigation still gives the Chinese source catalog a safe path.
      }
      window.location.reload()
    }),
  )

  panel.append(heading, detail, actions)
  main.append(panel)
  document.body.append(main)
  heading.focus()
}
