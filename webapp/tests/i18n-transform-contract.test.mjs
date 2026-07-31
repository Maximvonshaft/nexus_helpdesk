import assert from 'node:assert/strict'
import test from 'node:test'
import { transformPresentationSource } from '../scripts/vite-i18n-plugin.mjs'

const source = `
import { Box } from '@mui/material'

const navigation = { label: '案例处理' }

export function Demo({ customerText, userLabel }) {
  const status = '已关闭'
  const closed = status === '已关闭'
  return (
    <Box aria-label="主导航" title={\`当前账号：\${userLabel}\`}>
      页面不存在
      <span>{navigation.label}</span>
      <span>{customerText}</span>
      <span>{closed ? '返回案例处理' : '返回登录'}</span>
    </Box>
  )
}
`

test('transforms static presentation copy and preserves runtime customer content', () => {
  const messages = []
  const output = transformPresentationSource(
    source,
    '/repo/webapp/src/Demo.tsx',
    (message) => messages.push(message),
  )

  assert.ok(output)
  assert.match(output, /translateStatic as __nexusTranslateStatic/)
  assert.match(output, /translateTemplate as __nexusTranslateTemplate/)
  assert.match(output, /__nexusTranslateStatic\(/)
  assert.match(output, /__nexusTranslateTemplate\(/)
  assert.match(output, /customerText/)
  assert.match(output, /status === '已关闭'/)
  assert.deepEqual(
    new Set(messages),
    new Set([
      '案例处理',
      '已关闭',
      '主导航',
      '当前账号：{{0}}',
      '页面不存在',
      '返回案例处理',
      '返回登录',
    ]),
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
