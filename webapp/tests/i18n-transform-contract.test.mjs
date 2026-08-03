import assert from 'node:assert/strict'
import test from 'node:test'
import { transformPresentationSource } from '../scripts/vite-i18n-plugin.mjs'

const source = `
import { Box } from '@mui/material'

const navigation = { label: '案例处理' }

export function Demo({ customerText, userLabel, messageCount }) {
  const status = '已关闭'
  const closed = status === '已关闭'
  return (
    <Box aria-label="主导航" title={\`当前账号：\${userLabel}\`}>
      页面不存在
      <span>{navigation.label}</span>
      <span>{customerText}</span>
      <span>{messageCount} 条新消息</span>
      <span>{closed ? '返回案例处理' : '返回登录'}</span>
    </Box>
  )
}
`

const multilineSource = `
export function VoiceOffer({ seconds, count }) {
  return (
    <>
      <span>
        接听机会将在 {seconds} 秒后轮转给下一位坐席。
      </span>
      <span>
        当前还有 {count} 个来电等待处理。
      </span>
    </>
  )
}
`

test('transforms static presentation copy and preserves runtime customer and control values', () => {
  const entries = []
  const transformed = transformPresentationSource(
    source,
    '/repo/webapp/src/Demo.tsx',
    (entry) => entries.push(entry),
  )

  assert.ok(transformed)
  const { code, map } = transformed
  assert.match(code, /translateStatic as __nexusTranslateStatic/)
  assert.match(code, /translateTemplate as __nexusTranslateTemplate/)
  assert.match(code, /__nexusTranslateStatic\(/)
  assert.match(code, /__nexusTranslateTemplate\(/)
  assert.match(code, /customerText/)
  assert.match(code, /const status = ['"]已关闭['"]/)
  assert.match(code, /status === ['"]已关闭['"]/)
  assert.match(code, /['"] ['"] \+ __nexusTranslateStatic\(/)
  assert.equal(map.version, 3)
  assert.ok(map.sourcesContent?.includes(source))
  assert.deepEqual(
    new Set(entries.map((entry) => entry.source)),
    new Set([
      '案例处理',
      '主导航',
      '当前账号：{{0}}',
      '页面不存在',
      '条新消息',
      '返回案例处理',
      '返回登录',
    ]),
  )
  assert.equal(new Set(entries.map((entry) => entry.key)).size, entries.length)
  assert.ok(entries.every((entry) => entry.file === 'src/Demo.tsx'))
})

test('preserves inline and variable-backed API payload facts', () => {
  const apiSource = `
import { supportApi } from './supportApi'
const payload = { status: '处理中', customer_note: '客户要求改址' }
export async function save() {
  await supportApi.updateTicket(payload)
  await supportApi.createTicket({ status: '待处理', reason: '客户催派' })
  return <span>保存成功</span>
}
`
  const entries = []
  const transformed = transformPresentationSource(
    apiSource,
    '/repo/webapp/src/save.tsx',
    (entry) => entries.push(entry),
  )
  assert.ok(transformed)
  assert.match(transformed.code, /status: ['"]处理中['"]/)
  assert.match(transformed.code, /customer_note: ['"]客户要求改址['"]/)
  assert.match(transformed.code, /status: ['"]待处理['"]/)
  assert.match(transformed.code, /reason: ['"]客户催派['"]/)
  assert.deepEqual(entries.map((entry) => entry.source), ['保存成功'])
})

test('preserves JSX separator spaces around multiline expressions', () => {
  const transformed = transformPresentationSource(
    multilineSource,
    '/repo/webapp/src/VoiceOffer.tsx',
  )
  assert.ok(transformed)
  assert.match(transformed.code, /__nexusTranslateStatic\([^\n]+\) \+ ['"] ['"]/)
  assert.match(transformed.code, /['"] ['"] \+ __nexusTranslateStatic\(/)
})

test('message keys remain stable for the same source occurrence', () => {
  const first = []
  const second = []
  transformPresentationSource(source, '/repo/webapp/src/Demo.tsx', (entry) => first.push(entry))
  transformPresentationSource(source, '/repo/webapp/src/Demo.tsx', (entry) => second.push(entry))
  assert.deepEqual(
    first.map((entry) => entry.key),
    second.map((entry) => entry.key),
  )
})

test('does not transform localization runtime modules', () => {
  const output = transformPresentationSource(
    `export const fallback = '中文原文'`,
    '/repo/webapp/src/i18n/catalog.ts',
  )
  assert.equal(output, null)
})

test('does not translate technical control-flow values', () => {
  const output = transformPresentationSource(
    `export function resolve(value) {\n  switch (value) {\n    case '处理中': return value === '处理中'\n    default: return false\n  }\n}`,
    '/repo/webapp/src/technical.ts',
  )
  assert.equal(output, null)
})
