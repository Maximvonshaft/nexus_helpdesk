import { createHash } from 'node:crypto'
import ts from 'typescript'

const CJK_RE = /[\u3400-\u9fff]/u
const RUNTIME_IMPORT = '@/i18n/runtime'
const TRANSLATE_IDENTIFIER = '__nexusTranslateStatic'
const TEMPLATE_IDENTIFIER = '__nexusTranslateTemplate'

function isModuleSpecifier(node) {
  const parent = node.parent
  return Boolean(
    parent
    && (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent))
    && parent.moduleSpecifier === node,
  )
}

function isPropertyName(node) {
  const parent = node.parent
  if (!parent) return false
  return Boolean(
    (
      ts.isPropertyAssignment(parent)
      || ts.isPropertyDeclaration(parent)
      || ts.isMethodDeclaration(parent)
      || ts.isPropertySignature(parent)
    )
    && parent.name === node
  ) || Boolean(ts.isEnumMember(parent) && parent.name === node)
}

function isTypePosition(node) {
  let parent = node.parent
  while (parent) {
    if (ts.isTypeNode(parent)) return true
    if (ts.isExpression(parent) || ts.isStatement(parent) || ts.isSourceFile(parent)) return false
    parent = parent.parent
  }
  return false
}

function isTechnicalControlFlowValue(node) {
  const parent = node.parent
  if (!parent) return false
  if (ts.isElementAccessExpression(parent) && parent.argumentExpression === node) return true
  if (ts.isCaseClause(parent) && parent.expression === node) return true
  if (
    ts.isBinaryExpression(parent)
    && [
      ts.SyntaxKind.EqualsEqualsEqualsToken,
      ts.SyntaxKind.ExclamationEqualsEqualsToken,
      ts.SyntaxKind.EqualsEqualsToken,
      ts.SyntaxKind.ExclamationEqualsToken,
    ].includes(parent.operatorToken.kind)
  ) {
    return true
  }
  return false
}

function isExcludedString(node, sourceFile) {
  if (!CJK_RE.test(node.text)) return true
  if (sourceFile.fileName.replaceAll('\\', '/').includes('/src/i18n/')) return true
  return (
    isModuleSpecifier(node)
    || isPropertyName(node)
    || isTypePosition(node)
    || isTechnicalControlFlowValue(node)
  )
}

function templatePattern(node) {
  const values = []
  let pattern = node.head.text
  node.templateSpans.forEach((span, index) => {
    values.push(span.expression)
    pattern += `{{${index}}}${span.literal.text}`
  })
  return { pattern, values }
}

function sourceIdentity(fileName) {
  const normalized = fileName.replaceAll('\\', '/')
  const marker = '/src/'
  const index = normalized.lastIndexOf(marker)
  return index >= 0 ? `src/${normalized.slice(index + marker.length)}` : normalized
}

function messageKey(file, kind, source, ordinal) {
  const filePrefix = file
    .replace(/^src\//, '')
    .replace(/\.(?:ts|tsx)$/, '')
    .replace(/[^a-zA-Z0-9]+/g, '.')
    .replace(/^\.+|\.+$/g, '')
    .toLowerCase()
  const digest = createHash('sha256')
    .update(`${file}\u0000${kind}\u0000${source}\u0000${ordinal}`)
    .digest('hex')
    .slice(0, 12)
  return `${filePrefix || 'operator-ui'}.${digest}`
}

function staticTranslationCall(factory, key, text) {
  return factory.createCallExpression(
    factory.createIdentifier(TRANSLATE_IDENTIFIER),
    undefined,
    [factory.createStringLiteral(key), factory.createStringLiteral(text)],
  )
}

function templateTranslationCall(factory, key, pattern, values) {
  return factory.createCallExpression(
    factory.createIdentifier(TEMPLATE_IDENTIFIER),
    undefined,
    [
      factory.createStringLiteral(key),
      factory.createStringLiteral(pattern),
      factory.createArrayLiteralExpression(values, false),
    ],
  )
}

function normalizeJsxText(value) {
  return value.replace(/\s+/g, ' ').trim()
}

function occurrenceKey(file, line) {
  return `${file}:${line}`
}

export function transformPresentationSource(code, id, collect = () => {}) {
  const scriptKind = id.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  const sourceFile = ts.createSourceFile(
    id,
    code,
    ts.ScriptTarget.Latest,
    true,
    scriptKind,
  )
  const file = sourceIdentity(sourceFile.fileName)
  const ordinals = new Map()
  let changed = false

  const transformer = (context) => {
    const { factory } = context

    const record = (source, node, kind) => {
      const ordinalIdentity = `${kind}\u0000${source}`
      const ordinal = ordinals.get(ordinalIdentity) || 0
      ordinals.set(ordinalIdentity, ordinal + 1)
      const key = messageKey(file, kind, source, ordinal)
      const line = sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1
      collect({ key, source, kind, file, line })
      return key
    }

    const visit = (node) => {
      if (
        ts.isJsxAttribute(node)
        && node.initializer
        && ts.isStringLiteral(node.initializer)
        && CJK_RE.test(node.initializer.text)
      ) {
        changed = true
        const key = record(node.initializer.text, node, 'jsx_attribute')
        return factory.updateJsxAttribute(
          node,
          node.name,
          factory.createJsxExpression(
            undefined,
            staticTranslationCall(factory, key, node.initializer.text),
          ),
        )
      }

      if (ts.isJsxText(node) && CJK_RE.test(node.text)) {
        const message = normalizeJsxText(node.text)
        if (!message) return node
        changed = true
        const key = record(message, node, 'jsx_text')
        return factory.createJsxExpression(
          undefined,
          staticTranslationCall(factory, key, message),
        )
      }

      if (
        (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
        && !isExcludedString(node, sourceFile)
      ) {
        changed = true
        const key = record(node.text, node, 'static_literal')
        return staticTranslationCall(factory, key, node.text)
      }

      if (ts.isTemplateExpression(node)) {
        const { pattern, values } = templatePattern(node)
        const normalizedPath = sourceFile.fileName.replaceAll('\\', '/')
        if (CJK_RE.test(pattern) && !normalizedPath.includes('/src/i18n/')) {
          changed = true
          const key = record(pattern, node, 'template')
          return templateTranslationCall(
            factory,
            key,
            pattern,
            values.map((value) => ts.visitNode(value, visit)),
          )
        }
      }

      return ts.visitEachChild(node, visit, context)
    }

    return (root) => ts.visitNode(root, visit)
  }

  const result = ts.transform(sourceFile, [transformer])
  let transformed = result.transformed[0]
  result.dispose()
  if (!changed) return null

  const runtimeImport = ts.factory.createImportDeclaration(
    undefined,
    ts.factory.createImportClause(
      false,
      undefined,
      ts.factory.createNamedImports([
        ts.factory.createImportSpecifier(
          false,
          ts.factory.createIdentifier('translateStatic'),
          ts.factory.createIdentifier(TRANSLATE_IDENTIFIER),
        ),
        ts.factory.createImportSpecifier(
          false,
          ts.factory.createIdentifier('translateTemplate'),
          ts.factory.createIdentifier(TEMPLATE_IDENTIFIER),
        ),
      ]),
    ),
    ts.factory.createStringLiteral(RUNTIME_IMPORT),
  )
  transformed = ts.factory.updateSourceFile(
    transformed,
    [runtimeImport, ...transformed.statements],
  )

  const printer = ts.createPrinter({ newLine: ts.NewLineKind.LineFeed })
  return printer.printFile(transformed)
}

export function nexusI18nTransformPlugin() {
  const inventory = new Map()

  return {
    name: 'nexus-static-presentation-i18n',
    enforce: 'pre',

    transform(code, id) {
      const cleanId = id.split('?')[0]
      const normalizedId = cleanId.replaceAll('\\', '/')
      if (!/\.(ts|tsx)$/.test(cleanId)) return null
      if (!normalizedId.includes('/src/') || normalizedId.includes('/src/i18n/')) return null

      const output = transformPresentationSource(
        code,
        cleanId,
        (entry) => {
          const row = inventory.get(entry.key) || {
            key: entry.key,
            source: entry.source,
            kind: entry.kind,
            occurrences: new Map(),
          }
          row.occurrences.set(occurrenceKey(entry.file, entry.line), {
            file: entry.file,
            line: entry.line,
          })
          inventory.set(entry.key, row)
        },
      )
      return output ? { code: output, map: null } : null
    },

    generateBundle() {
      const messages = [...inventory.values()]
        .sort((left, right) => left.key.localeCompare(right.key))
        .map((row) => ({
          key: row.key,
          source: row.source,
          kind: row.kind,
          occurrences: [...row.occurrences.values()].sort((left, right) => (
            left.file.localeCompare(right.file) || left.line - right.line
          )),
        }))

      this.emitFile({
        type: 'asset',
        fileName: 'i18n-inventory.json',
        source: `${JSON.stringify({ schema_version: 2, messages }, null, 2)}\n`,
      })
    },
  }
}
