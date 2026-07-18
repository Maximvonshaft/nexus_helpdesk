import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, extname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEBAPP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SOURCE_ROOT = resolve(WEBAPP_ROOT, 'src')
const AUTHORITY = 'src/lib/apiClient.ts'
const SOURCE_EXTENSIONS = new Set(['.js', '.jsx', '.ts', '.tsx'])

const forbidden = [
  {
    name: 'native fetch lifecycle',
    pattern: /(^|[^\w$.])fetch\s*\(/m,
  },
  {
    name: 'API base URL ownership',
    pattern: /import\.meta\.env\.VITE_API_BASE_URL/,
  },
  {
    name: 'operator authentication token storage',
    pattern: /helpdesk-webapp-token/,
  },
  {
    name: 'Authorization header assembly',
    pattern: /(?:\.set|\.append)\s*\(\s*['"]Authorization['"]|['"]Authorization['"]\s*:/,
  },
  {
    name: 'global 401 lifecycle',
    pattern: /authExpiryHandled|class\s+AuthExpiredError\b/,
  },
]

function walk(directory) {
  return readdirSync(directory)
    .flatMap((name) => {
      const path = resolve(directory, name)
      return statSync(path).isDirectory() ? walk(path) : [path]
    })
    .filter((path) => SOURCE_EXTENSIONS.has(extname(path)))
}

const failures = []
for (const path of walk(SOURCE_ROOT)) {
  const repositoryPath = relative(WEBAPP_ROOT, path).replaceAll('\\', '/')
  if (repositoryPath === AUTHORITY) continue
  const source = readFileSync(path, 'utf8')
  for (const rule of forbidden) {
    if (rule.pattern.test(source)) {
      failures.push(`${repositoryPath}: owns ${rule.name}; delegate to ${AUTHORITY}`)
    }
  }
}

if (failures.length) {
  console.error('HTTP transport authority violations:')
  failures.forEach((failure) => console.error(`- ${failure}`))
  process.exit(1)
}

console.log(`HTTP transport authority verified: ${AUTHORITY}`)
