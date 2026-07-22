import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(path, 'utf8')
const governanceApi = read('src/lib/governanceApi.ts')
const administration = read('src/features/administration/AdministrationPage.tsx')
const roleTemplates = read('src/features/administration/RoleTemplatesPanel.tsx')
const agentControl = read('src/features/agent-control/AgentControlPage.tsx')
const knowledgeLazy = read('src/features/knowledge/lazy.tsx')
const navigation = read('src/app/navigation.ts')
const routes = read('src/routes/administration.tsx')

for (const endpoint of [
  '/api/governance/role-templates',
  '/api/governance/markets',
  '/api/governance/knowledge-imports',
  '/api/governance/deployments/',
]) {
  assert.ok(governanceApi.includes(endpoint), `governance API must expose ${endpoint}`)
}

assert.match(administration, /RoleTemplatesPanel/)
assert.match(administration, /MarketGovernancePanel/)
assert.match(administration, /market\.manage/)
assert.match(agentControl, /ReleaseDeliveryPanel/)
assert.match(agentControl, /value="delivery"/)
assert.match(knowledgeLazy, /KnowledgeImportPanel/)
assert.match(knowledgeLazy, /KnowledgePage/)
assert.match(navigation, /market\.manage/)
assert.match(routes, /market\.manage/)
assert.match(roleTemplates, /await governanceApi\.updateRoleTemplate\(id, payload\)/)
assert.match(roleTemplates, /return governanceApi\.publishRoleTemplate\(id, '运营控制面发布'\)/)

for (const forbidden of [
  '/governance',
  '/role-templates',
  '/market-governance',
  '/release-delivery',
]) {
  assert.ok(!navigation.includes(`currentHref: '${forbidden}'`), `must not introduce parallel product route ${forbidden}`)
}

for (const source of [governanceApi, administration, roleTemplates, agentControl, knowledgeLazy]) {
  assert.ok(!source.includes('localStorage'), 'governance state must remain server authoritative')
  assert.ok(!source.includes('mock'), 'governance product must not use mock state')
}

console.log('governance completion frontend contract: ok')
