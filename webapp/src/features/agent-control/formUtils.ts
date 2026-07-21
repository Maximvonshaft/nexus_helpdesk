import type { AgentConfigResource } from '@/lib/types'

export function lines(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item || '').trim()).filter(Boolean)
  return String(value || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

export function lineText(value: unknown): string {
  return lines(value).join('\n')
}

export function resourceByType(resources: AgentConfigResource[], type: AgentConfigResource['config_type']) {
  return resources.find((item) => item.config_type === type) ?? null
}

export function contentOf(resource?: AgentConfigResource | null): Record<string, unknown> {
  if (resource?.draft_content_json && typeof resource.draft_content_json === 'object') return { ...resource.draft_content_json }
  if (resource?.published_content_json && typeof resource.published_content_json === 'object') return { ...resource.published_content_json }
  return {}
}

export function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : value == null ? fallback : String(value)
}

export function asNumber(value: unknown, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

export function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

export function parseSchemaFields(value: string) {
  const properties: Record<string, Record<string, unknown>> = {}
  const required: string[] = []
  lines(value).forEach((row) => {
    const [rawName, rawType = 'string', rawRequired = ''] = row.split(':').map((part) => part.trim())
    const name = rawName.replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^[_\-.]+|[_\-.]+$/g, '').slice(0, 120)
    if (!name) return
    const type = ['string', 'integer', 'number', 'boolean', 'object', 'array'].includes(rawType) ? rawType : 'string'
    properties[name] = { type }
    if (['required', 'yes', 'true', '1', '*'].includes(rawRequired.toLowerCase())) required.push(name)
  })
  return { type: 'object', properties, required, additionalProperties: false }
}

export function schemaFieldsText(value: unknown) {
  if (!value || typeof value !== 'object') return ''
  const schema = value as { properties?: Record<string, { type?: string }>; required?: string[] }
  const required = new Set(schema.required ?? [])
  return Object.entries(schema.properties ?? {})
    .map(([name, item]) => `${name}:${item.type || 'string'}${required.has(name) ? ':required' : ''}`)
    .join('\n')
}
