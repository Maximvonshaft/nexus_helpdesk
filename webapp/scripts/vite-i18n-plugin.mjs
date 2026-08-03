import { createHash } from 'node:crypto'
import ts from 'typescript'

const CJK_RE = /[\u3400-\u9fff]/u
const RUNTIME_IMPORT = '@/i18n/runtime'
const TRANSLATE_IDENTIFIER = '__nexusTranslateStatic'
const TEMPLATE_IDENTIFIER = '__nexusTranslateTemplate'
const TECHNICAL_CALL_RE = /(?:^|\.)(?:apiRequest|staticJsonAssetRequest|fetch|mutate|mutateAsync|send|dispatch|emit|track|log|setItem|removeItem|setQueryData)$|(?:^|\.)[^.]*Api\./iu

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

function expressionIdentity(node) {
  if (ts.isIdentifier(node)) return node.text
  if (ts.isPropertyAccessExpression(node)) {
    const owner = expressionIdentity(node.expression)
    return owner ? `${owner}.${node.name.text}` : node.name.text
  }
  return ''
}

function technicalReference(node, identifiers, properties) {
  if (ts.isIdentifier(node)) {
    identifiers.add(node.text)
    return
  }
  if (ts.isPropertyAccessExpression(node)) {
    properties.add(node.name.text)
    technicalReference(node.expression, identifiers, properties)
    return
  }
  if (ts.isElementAccessExpression(node)) {
    technicalReference(node.expression, identifiers, properties)
    if (ts.isStringLiteral(node.argumentExpression)) properties.add(node.argumentExpression.text)
  }
}

function technicalFacts(sourceFile) {
  const identifiers = new Set()
  const properties = new Set()

  const visit = (node) => {
    if (
      ts.isBinaryExpression(node)
      && [
        ts.SyntaxKind.EqualsEqualsEqualsToken,
        ts.SyntaxKind.ExclamationEqualsEqualsToken,
        ts.SyntaxKind.EqualsEqualsToken,
        ts.SyntaxKind.ExclamationEqualsToken,
      ].includes(node.operatorToken.kind)
    ) {
      technicalReference(node.left, identifiers, properties)
      technicalReference(node.right, identifiers, properties)
    }

    if (ts.isSwitchStatement(node)) {
      technicalReference(node.expression, identifiers, properties)
    }

    if (ts.isCallExpression(node) && TECHNICAL_CALL_RE.test(expressionIdentity(node.expression))) {
      node.arguments.forEach((argument) => technicalReference(argument, identifiers, properties))
    }

    ts.forEachChild(node, visit)
  }
  visit(sourceFile)
  return { identifiers, properties }
}

function propertyNameText(name) {
  if (ts.isIdentifier(name) || ts.isStringLiteral(name) || ts.isNumericLiteral(name)) return name.text
  return ''
}

function isInsideTechnicalCall(node) {
  let current = node
  while (current.parent) {
    const parent = current.parent
    if (ts.isCallExpression(parent)) {
      const isArgument = parent.arguments.some((argument) => (
        argument === current
        || (current.pos >= argument.pos && current.end <= argument.end)
      ))
      return isArgument && TECHNICAL_CALL_RE.test(expressionIdentity(parent.expression))
    }
    if (ts.isStatement(parent) || ts.isSourceFile(parent)) return false
    current = parent
  }
  return false
}

function isInsideTechnicalVariable(node, facts) {
  let current = node
  while (current.parent) {
    const parent = current.parent
    if (ts.isVariableDeclaration(parent) && parent.initializer) {
      return Boolean(
        ts.isIdentifier(parent.name)
        && facts.identifiers.has(parent.name.text)
        && current.pos >= parent.initializer.pos
        && current.end <= parent.initializer.end
      )
    }
    if (ts.isStatement(parent) || ts.isSourceFile(parent)) return false
    current = parent
  }
  return false
}

function isTechnicalDataValue(node, facts) {
  const parent = node.parent
  if (!parent) return false

  if (
    ts.isVariableDeclaration(parent)
    && parent.initializer === node
    && ts.isIdentifier(parent.name)
    && facts.identifiers.has(parent.name.text)
  ) return true

  if (
    ts.isBinaryExpression(parent)
    && parent.right === node
    && parent.operatorToken.kind === ts.SyntaxKind.EqualsToken
    && ts.isIdentifier(parent.left)
    && facts.identifiers.has(parent.left.text)
  ) return true

  if (
    ts.isPropertyAssignment(parent)
    && parent.initializer === node
    && facts.properties.has(propertyNameText(parent.name))
  ) return true

  return isInsideTechnicalCall(node) || isInsideTechnicalVariable(node, facts)
}

function isExcludedString(node, sourceFile, facts) {
  if (!CJK_RE.test(node.text)) return true
  if (sourceFile.fileName.replaceAll('\\', '/').includes('/src/i18n/')) return true
  return (
    isModuleSpecifier(node)
    || isPropertyName(node)
    || isTypePosition(node)
    || isTechnicalControlFlowValue(node)
    || isTechnicalDataValue(node, facts)
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

// Match React's JSX literal cleaning semantics. Indentation-only lines disappear,
// while meaningful spaces next to expressions survive even across newlines.
function cleanJsxText(value) {
  const lines = value.replace(/\r\n?/g, '\n').split('\n')
  let lastNonEmptyLine = 0
  for (let index = 0; index < lines.length; index += 1) {
    if (/[^\t ]/.test(lines[index])) lastNonEmptyLine = index
  }

  let result = ''
  for (let index = 0; index < lines.length; index += 1) {
    let line = lines[index].replace(/\t/g, ' ')
    if (index !== 0) line = line.replace(/^ +/, '')
    if (index !== lines.length - 1) line = line.replace(/ +$/, '')
    if (!line) continue
    result += line
    if (index !== lastNonEmptyLine) result += ' '
  }
  return result
}

function normalizeJsxText(value) {
  const effective = cleanJsxText(value)
  return {
    message: effective.trim(),
    prefix: effective.startsWith(' ') ? ' ' : '',
    suffix: effective.endsWith(' ') ? ' ' : '',
  }
}

function withBoundaryWhitespace(factory, expression, prefix, suffix) {
  let result = expression
  if (prefix) {
    result = factory.createBinaryExpression(
      factory.createStringLiteral(prefix),
      factory.createToken(ts.SyntaxKind.PlusToken),
      result,
    )
  }
  if (suffix) {
    result = factory.createBinaryExpression(
      result,
      factory.createToken(ts.SyntaxKind.PlusToken),
      factory.createStringLiteral(suffix),
    )
  }
  return result
}

function occurrenceKey(file, line) {
  return `${file}:${line}`
}

function runtimeImport(factory) {
  return factory.createImportDeclaration(
    undefined,
    factory.createImportClause(
      false,
      undefined,
      factory.createNamedImports([
        factory.createImportSpecifier(
          false,
          factory.createIdentifier('translateStatic'),
          factory.createIdentifier(TRANSLATE_IDENTIFIER),
        ),
        factory.createImportSpecifier(
          false,
          factory.createIdentifier('translateTemplate'),
          factory.createIdentifier(TEMPLATE_IDENTIFIER),
        ),
      ]),
    ),
    factory.createStringLiteral(RUNTIME_IMPORT),
  )
}

function flattenDiagnostics(diagnostics) {
  return diagnostics
    .filter((diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error)
    .map((diagnostic) => ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n'))
}

export function transformPresentationSource(code, id, collect = () => {}) {
  const file = sourceIdentity(id)
  const ordinals = new Map()
  let changed = false

  const transformer = (context) => {
    const { factory } = context

    const record = (source, node, kind, sourceFile) => {
      const ordinalIdentity = `${kind}\u0000${source}`
      const ordinal = ordinals.get(ordinalIdentity) || 0
      ordinals.set(ordinalIdentity, ordinal + 1)
      const key = messageKey(file, kind, source, ordinal)
      const line = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1
      collect({ key, source, kind, file, line })
      return key
    }

    const visitFor = (sourceFile) => {
      const facts = technicalFacts(sourceFile)
      const visit = (node) => {
        if (
          ts.isJsxAttribute(node)
          && node.initializer
          && ts.isStringLiteral(node.initializer)
          && CJK_RE.test(node.initializer.text)
        ) {
          changed = true
          const key = record(node.initializer.text, node, 'jsx_attribute', sourceFile)
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
          const { message, prefix, suffix } = normalizeJsxText(node.text)
          if (!message) return node
          changed = true
          const key = record(message, node, 'jsx_text', sourceFile)
          return factory.createJsxExpression(
            undefined,
            withBoundaryWhitespace(
              factory,
              staticTranslationCall(factory, key, message),
              prefix,
              suffix,
            ),
          )
        }

        if (
          (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
          && !isExcludedString(node, sourceFile, facts)
        ) {
          changed = true
          const key = record(node.text, node, 'static_literal', sourceFile)
          return staticTranslationCall(factory, key, node.text)
        }

        if (ts.isTemplateExpression(node)) {
          const { pattern, values } = templatePattern(node)
          if (
            CJK_RE.test(pattern)
            && !sourceFile.fileName.replaceAll('\\', '/').includes('/src/i18n/')
            && !isTechnicalDataValue(node, facts)
          ) {
            changed = true
            const key = record(pattern, node, 'template', sourceFile)
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
      return visit
    }

    return (sourceFile) => {
      const visited = ts.visitNode(sourceFile, visitFor(sourceFile))
      if (!changed) return visited
      return factory.updateSourceFile(
        visited,
        [runtimeImport(factory), ...visited.statements],
      )
    }
  }

  const result = ts.transpileModule(code, {
    fileName: id,
    compilerOptions: {
      target: ts.ScriptTarget.ESNext,
      module: ts.ModuleKind.ESNext,
      jsx: ts.JsxEmit.Preserve,
      sourceMap: true,
      inlineSources: true,
      inlineSourceMap: false,
      newLine: ts.NewLineKind.LineFeed,
      removeComments: false,
    },
    transformers: { before: [transformer] },
    reportDiagnostics: true,
  })
  const errors = flattenDiagnostics(result.diagnostics || [])
  if (errors.length > 0) {
    throw new Error(`i18n_transform_failed:${file}:${errors.join('|')}`)
  }
  if (!changed) return null
  if (!result.sourceMapText) throw new Error(`i18n_source_map_missing:${file}`)

  return {
    code: result.outputText.replace(/\n?\/\/# sourceMappingURL=.*$/u, ''),
    map: JSON.parse(result.sourceMapText),
  }
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
      if (!CJK_RE.test(code)) return null

      return transformPresentationSource(
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
