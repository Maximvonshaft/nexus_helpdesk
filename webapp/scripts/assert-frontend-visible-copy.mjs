import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { extname, join, relative, resolve } from 'node:path'
import ts from 'typescript'

const webappRoot = resolve(import.meta.dirname, '..')
const srcRoot = join(webappRoot, 'src')
const forbiddenVisibleTerms = /\b(?:AI|Artificial Intelligence|Runtime|Provider|RAG|Prompt|Model|Agent)\b/i
const visibleNamePattern = /(?:label|title|description|message|summary|placeholder|eyebrow|caption|copy|text|detail)$/i
const visibleSetterPattern = /^set.*(?:error|message|notice|title|description|copy)$/i

function walk(root) {
  if (!existsSync(root)) return []
  const files = []
  for (const name of readdirSync(root)) {
    const path = join(root, name)
    const stat = statSync(path)
    if (stat.isDirectory()) files.push(...walk(path))
    else files.push(path)
  }
  return files
}

function read(path) {
  return readFileSync(path, 'utf8')
}

function relativePath(path) {
  return relative(webappRoot, path).replaceAll('\\', '/')
}

function nodeName(node) {
  if (!node) return ''
  if (ts.isIdentifier(node) || ts.isStringLiteral(node) || ts.isNumericLiteral(node)) return node.text
  return ''
}

function collectLiteralValues(node, values) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    values.push(node.text)
    return
  }
  if (ts.isTemplateExpression(node)) {
    values.push(node.head.text)
    for (const span of node.templateSpans) values.push(span.literal.text)
    return
  }
  if (ts.isConditionalExpression(node)) {
    collectLiteralValues(node.whenTrue, values)
    collectLiteralValues(node.whenFalse, values)
    return
  }
  if (ts.isArrayLiteralExpression(node)) {
    for (const element of node.elements) collectLiteralValues(element, values)
  }
}

function isDocumentTitleAssignment(node) {
  return ts.isBinaryExpression(node)
    && node.operatorToken.kind === ts.SyntaxKind.EqualsToken
    && ts.isPropertyAccessExpression(node.left)
    && ts.isIdentifier(node.left.expression)
    && node.left.expression.text === 'document'
    && node.left.name.text === 'title'
}

function collectVisibleValues(path) {
  const source = read(path)
  const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const sourceFile = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, kind)
  const values = []

  function collectJsxExpression(node) {
    collectLiteralValues(node, values)
    ts.forEachChild(node, collectJsxExpression)
  }

  function visit(node) {
    if (ts.isJsxText(node)) values.push(node.getText(sourceFile))
    if (ts.isJsxAttribute(node) && node.initializer) {
      if (ts.isStringLiteral(node.initializer)) values.push(node.initializer.text)
      else if (ts.isJsxExpression(node.initializer) && node.initializer.expression) collectJsxExpression(node.initializer.expression)
    }
    if (ts.isJsxExpression(node) && node.expression) collectJsxExpression(node.expression)

    if (ts.isPropertyAssignment(node) && visibleNamePattern.test(nodeName(node.name))) {
      collectLiteralValues(node.initializer, values)
    }
    if (ts.isVariableDeclaration(node) && visibleNamePattern.test(nodeName(node.name)) && node.initializer) {
      collectLiteralValues(node.initializer, values)
    }
    if (isDocumentTitleAssignment(node)) collectLiteralValues(node.right, values)
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && visibleSetterPattern.test(node.expression.text)) {
      for (const argument of node.arguments) collectLiteralValues(argument, values)
    }

    ts.forEachChild(node, visit)
  }

  visit(sourceFile)
  return values
}

const findings = []
for (const path of walk(srcRoot).filter((value) => ['.ts', '.tsx'].includes(extname(value)))) {
  for (const value of collectVisibleValues(path)) {
    if (forbiddenVisibleTerms.test(value)) findings.push(`${relativePath(path)}: ${value.trim()}`)
  }
}

const indexHtml = read(join(webappRoot, 'index.html'))
for (const match of indexHtml.matchAll(/<(?:title|meta)\b[^>]*?(?:content=["']([^"']*)["'])?[^>]*>([^<]*)/gi)) {
  const value = `${match[1] ?? ''} ${match[2] ?? ''}`.trim()
  if (forbiddenVisibleTerms.test(value)) findings.push(`index.html: ${value}`)
}

assert.deepEqual(findings, [], `operator-visible internal terminology remains in display contexts:\n${findings.join('\n')}`)

console.log(JSON.stringify({ ok: true, findings: findings.length }, null, 2))
