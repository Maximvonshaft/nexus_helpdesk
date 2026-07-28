import type { EmbeddedSignupFinish, EmbeddedSignupSession } from '@/lib/whatsappTypes'

type FacebookLoginResponse = {
  authResponse?: {
    code?: string
  }
  status?: string
}

type FacebookSdk = {
  init: (options: { appId: string; version: string; cookie: boolean; xfbml: boolean }) => void
  login: (
    callback: (response: FacebookLoginResponse) => void,
    options: {
      config_id: string
      response_type: 'code'
      override_default_response_type: true
      extras: {
        setup: Record<string, never>
        sessionInfoVersion: '3'
      }
    },
  ) => void
}

declare global {
  interface Window {
    FB?: FacebookSdk
    fbAsyncInit?: () => void
  }
}

const SDK_ID = 'meta-facebook-jssdk'
const SDK_SRC = 'https://connect.facebook.net/en_US/sdk.js'

let sdkPromise: Promise<FacebookSdk> | null = null

export async function launchMetaEmbeddedSignup(
  session: EmbeddedSignupSession,
): Promise<{ code: string; finish: EmbeddedSignupFinish }> {
  if (window.location.origin !== session.allowed_origin) {
    throw new Error('embedded_signup_origin_mismatch')
  }
  const sdk = await loadMetaSdk(session.app_id, session.graph_api_version)
  const finishPromise = waitForSignupFinish(session.expires_at)
  const codePromise = new Promise<string>((resolve, reject) => {
    sdk.login((response) => {
      const code = response.authResponse?.code?.trim()
      if (!code) {
        reject(new Error(response.status === 'unknown' ? 'embedded_signup_cancelled' : 'embedded_signup_code_missing'))
        return
      }
      resolve(code)
    }, {
      config_id: session.configuration_id,
      response_type: 'code',
      override_default_response_type: true,
      extras: {
        setup: {},
        sessionInfoVersion: '3',
      },
    })
  })
  try {
    const [code, finish] = await Promise.all([codePromise, finishPromise])
    return { code, finish }
  } catch (error) {
    finishPromise.catch(() => undefined)
    throw error
  }
}

function loadMetaSdk(appId: string, version: string): Promise<FacebookSdk> {
  if (window.FB) {
    window.FB.init({ appId, version, cookie: true, xfbml: false })
    return Promise.resolve(window.FB)
  }
  if (sdkPromise) return sdkPromise

  document.getElementById(SDK_ID)?.remove()

  sdkPromise = new Promise<FacebookSdk>((resolve, reject) => {
    let script: HTMLScriptElement | null = null
    const resetMetaSdkLoad = (code: string) => {
      window.clearTimeout(timeout)
      if (script?.isConnected) {
        script.remove()
      } else {
        document.getElementById(SDK_ID)?.remove()
      }
      sdkPromise = null
      reject(new Error(code))
    }
    const timeout = window.setTimeout(() => {
      resetMetaSdkLoad('meta_sdk_load_timeout')
    }, 15_000)
    window.fbAsyncInit = () => {
      window.clearTimeout(timeout)
      if (!window.FB) {
        resetMetaSdkLoad('meta_sdk_unavailable')
        return
      }
      window.FB.init({ appId, version, cookie: true, xfbml: false })
      resolve(window.FB)
    }
    script = document.createElement('script')
    script.id = SDK_ID
    script.src = SDK_SRC
    script.async = true
    script.defer = true
    script.crossOrigin = 'anonymous'
    script.referrerPolicy = 'origin'
    script.onerror = () => {
      resetMetaSdkLoad('meta_sdk_load_failed')
    }
    document.head.appendChild(script)
  })
  return sdkPromise
}

function waitForSignupFinish(expiresAt: string): Promise<EmbeddedSignupFinish> {
  return new Promise((resolve, reject) => {
    const expiry = Date.parse(expiresAt)
    const timeoutMs = Number.isFinite(expiry)
      ? Math.max(1_000, Math.min(expiry - Date.now(), 15 * 60_000))
      : 10 * 60_000
    const timeout = window.setTimeout(() => {
      cleanup()
      reject(new Error('embedded_signup_finish_timeout'))
    }, timeoutMs)

    const listener = (event: MessageEvent) => {
      const trustedOrigin = event.origin === 'https://www.facebook.com'
        || event.origin === 'https://web.facebook.com'
      if (!trustedOrigin || event.source === null || event.source === window) return
      const payload = parseMetaMessage(event.data)
      if (!payload || payload.type !== 'WA_EMBEDDED_SIGNUP') return
      if (payload.event === 'CANCEL') {
        cleanup()
        reject(new Error('embedded_signup_cancelled'))
        return
      }
      if (payload.event !== 'FINISH') return
      const data = payload.data
      const wabaId = stringValue(data?.waba_id)
      const phoneNumberId = stringValue(data?.phone_number_id)
      if (!wabaId || !phoneNumberId) {
        cleanup()
        reject(new Error('embedded_signup_assets_missing'))
        return
      }
      cleanup()
      resolve({
        business_account_id: stringValue(data?.business_id),
        waba_id: wabaId,
        phone_number_id: phoneNumberId,
      })
    }

    const cleanup = () => {
      window.clearTimeout(timeout)
      window.removeEventListener('message', listener)
    }
    window.addEventListener('message', listener)
  })
}

function parseMetaMessage(value: unknown): { type?: unknown; event?: unknown; data?: Record<string, unknown> } | null {
  let candidate = value
  if (typeof candidate === 'string') {
    try {
      candidate = JSON.parse(candidate)
    } catch {
      return null
    }
  }
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return null
  return candidate as { type?: unknown; event?: unknown; data?: Record<string, unknown> }
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}
