import { setRecoveryUiLocale } from './localeAuthority'
import type { UiLocale } from './localeAuthority'

const copy: Record<Exclude<UiLocale, 'zh-CN'>, {
  title: string
  detail: string
  retry: string
  chinese: string
  recoveryUnavailable: string
}> = {
  en: {
    title: 'The interface language could not be loaded',
    detail: 'Nexus did not start because the complete English catalog is unavailable. No customer or operational data has been changed.',
    retry: 'Try again',
    chinese: 'Use Simplified Chinese',
    recoveryUnavailable: 'This browser could not retain the recovery language. Nexus was not reloaded; enable session storage or try again in another browser session.',
  },
  de: {
    title: 'Die Oberflächensprache konnte nicht geladen werden',
    detail: 'Nexus wurde nicht gestartet, weil der vollständige deutsche Katalog nicht verfügbar ist. Kunden- und Betriebsdaten wurden nicht geändert.',
    retry: 'Erneut versuchen',
    chinese: 'Vereinfachtes Chinesisch verwenden',
    recoveryUnavailable: 'Der Browser konnte die Wiederherstellungssprache nicht speichern. Nexus wurde nicht neu geladen. Aktivieren Sie den Sitzungsspeicher oder verwenden Sie eine andere Browsersitzung.',
  },
  cnr: {
    title: 'Jezik interfejsa nije moguće učitati',
    detail: 'Nexus nije pokrenut jer kompletan crnogorski katalog nije dostupan. Podaci o korisnicima i operativni podaci nijesu izmijenjeni.',
    retry: 'Pokušaj ponovo',
    chinese: 'Koristi pojednostavljeni kineski',
    recoveryUnavailable: 'Pregledač nije mogao sačuvati jezik za oporavak. Nexus nije ponovo učitan; omogućite skladište sesije ili pokušajte u drugoj sesiji pregledača.',
  },
}

function button(label: string, primary: boolean, onClick: () => void) {
  const element = document.createElement('button')
  element.type = 'button'
  element.textContent = label
  element.style.minHeight = '44px'
  element.style.padding = '10px 18px'
  element.style.borderRadius = '8px'
  element.style.border = primary ? '1px solid LinkText' : '1px solid GrayText'
  element.style.background = primary ? 'LinkText' : 'ButtonFace'
  element.style.color = primary ? 'Canvas' : 'ButtonText'
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
  document.body.style.background = 'Canvas'
  document.body.style.color = 'CanvasText'

  const main = document.createElement('main')
  main.setAttribute('role', 'main')
  main.style.minHeight = '100dvh'
  main.style.display = 'grid'
  main.style.placeItems = 'center'
  main.style.padding = '24px'

  const panel = document.createElement('section')
  panel.style.width = 'min(100%, 560px)'
  panel.style.padding = '28px'
  panel.style.border = '1px solid GrayText'
  panel.style.borderRadius = '12px'
  panel.style.background = 'Canvas'

  const heading = document.createElement('h1')
  heading.tabIndex = -1
  heading.textContent = content.title
  heading.style.margin = '0'
  heading.style.font = '700 24px/1.3 system-ui, sans-serif'

  const detail = document.createElement('p')
  detail.textContent = content.detail
  detail.style.margin = '14px 0 22px'
  detail.style.font = '400 16px/1.6 system-ui, sans-serif'

  const status = document.createElement('p')
  status.setAttribute('role', 'alert')
  status.hidden = true
  status.style.margin = '0 0 18px'
  status.style.padding = '12px'
  status.style.border = '1px solid GrayText'
  status.style.borderRadius = '8px'
  status.style.font = '600 14px/1.5 system-ui, sans-serif'

  const actions = document.createElement('div')
  actions.style.display = 'flex'
  actions.style.flexWrap = 'wrap'
  actions.style.gap = '12px'
  const recoveryButton = button(content.chinese, false, () => {
    if (!setRecoveryUiLocale('zh-CN')) {
      status.textContent = content.recoveryUnavailable
      status.hidden = false
      recoveryButton.focus()
      return
    }
    window.location.reload()
  })
  actions.append(
    button(content.retry, true, () => window.location.reload()),
    recoveryButton,
  )

  panel.append(heading, detail, status, actions)
  main.append(panel)
  document.body.append(main)
  heading.focus()
}
