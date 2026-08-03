import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, extname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEBAPP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SOURCE_ROOT = resolve(WEBAPP_ROOT, 'src')
const API_AUTHORITY = 'src/lib/apiClient.ts'
const FETCH_AUTHORITY = 'src/lib/httpTransport.ts'
const SOURCE_EXTENSIONS = new Set(['.js', '.jsx', '.ts', '.tsx'])

const forbidden = [
  {
    name: 'native fetch lifecycle',
    authority: FETCH_AUTHORITY,
    pattern: /(^|[^\w$.])fetch\s*\(/m,
  },
  {
    name: 'API base URL ownership',
    authority: API_AUTHORITY,
    pattern: /import\.meta\.env\.VITE_API_BASE_URL/,
  },
  {
    name: 'operator authentication token storage',
    authority: API_AUTHORITY,
    pattern: /helpdesk-webapp-token/,
  },
  {
    name: 'Authorization header assembly',
    authority: API_AUTHORITY,
    pattern: /(?:\.set|\.append)\s*\(\s*['"]Authorization['"]|['"]Authorization['"]\s*:/,
  },
  {
    name: 'global 401 lifecycle',
    authority: API_AUTHORITY,
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
  const source = readFileSync(path, 'utf8')
  for (const rule of forbidden) {
    if (repositoryPath === rule.authority) continue
    if (rule.pattern.test(source)) {
      failures.push(`${repositoryPath}: owns ${rule.name}; delegate to ${rule.authority}`)
    }
  }
}

if (failures.length) {
  console.error('HTTP transport authority violations:')
  failures.forEach((failure) => console.error(`- ${failure}`))
  process.exit(1)
}

console.log(`HTTP transport authorities verified: fetch=${FETCH_AUTHORITY} api=${API_AUTHORITY}`)
