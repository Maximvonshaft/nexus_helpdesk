import path from 'node:path'
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

function staticTranslationCall(factory, text) {
  return factory.createCallExpression(
    factory.createIdentifier(TRANSLATE_IDENTIFIER),
    undefined,
    [factory.createStringLiteral(text)],
  )
}

function templateTranslationCall(factory, pattern, values) {
  return factory.createCallExpression(
    factory.createIdentifier(TEMPLATE_IDENTIFIER),
    undefined,
    [
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
  let changed = false

  const transformer = (context) => {
    const { factory } = context

    const record = (message, node) => {
      const line = sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1
      collect(message, sourceFile.fileName, line)
    }

    const visit = (node) => {
      if (
        ts.isJsxAttribute(node)
        && node.initializer
        && ts.isStringLiteral(node.initializer)
        && CJK_RE.test(node.initializer.text)
      ) {
        changed = true
        record(node.initializer.text, node)
        return factory.updateJsxAttribute(
          node,
          node.name,
          factory.createJsxExpression(
            undefined,
            staticTranslationCall(factory, node.initializer.text),
          ),
        )
      }

      if (ts.isJsxText(node) && CJK_RE.test(node.text)) {
        const message = normalizeJsxText(node.text)
        if (!message) return node
        changed = true
        record(message, node)
        return factory.createJsxExpression(
          undefined,
          staticTranslationCall(factory, message),
        )
      }

      if (
        (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
        && !isExcludedString(node, sourceFile)
      ) {
        changed = true
        record(node.text, node)
        return staticTranslationCall(factory, node.text)
      }

      if (ts.isTemplateExpression(node)) {
        const { pattern, values } = templatePattern(node)
        const normalizedPath = sourceFile.fileName.replaceAll('\\', '/')
        if (CJK_RE.test(pattern) && !normalizedPath.includes('/src/i18n/')) {
          changed = true
          record(pattern, node)
          return templateTranslationCall(
            factory,
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
        (message, file, line) => {
          const relativeFile = path.relative(process.cwd(), file).replaceAll('\\', '/')
          const occurrences = inventory.get(message) || new Map()
          occurrences.set(occurrenceKey(relativeFile, line), {
            file: relativeFile,
            line,
          })
          inventory.set(message, occurrences)
        },
      )
      return output ? { code: output, map: null } : null
    },

    generateBundle() {
      const messages = [...inventory.entries()]
        .sort(([left], [right]) => left.localeCompare(right, 'zh-CN'))
        .map(([source, occurrences]) => ({
          source,
          occurrences: [...occurrences.values()].sort((left, right) => (
            left.file.localeCompare(right.file) || left.line - right.line
          )),
        }))

      this.emitFile({
        type: 'asset',
        fileName: 'i18n-inventory.json',
        source: `${JSON.stringify({ schema_version: 1, messages }, null, 2)}\n`,
      })
    },
  }
}
