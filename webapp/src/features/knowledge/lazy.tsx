import { Stack } from '@mui/material'
import { KnowledgeImportPanel } from './KnowledgeImportPanel'
import { KnowledgePage } from './KnowledgePage'

export default function CanonicalKnowledgePage({ canManage }: { canManage: boolean }) {
  return (
    <Stack spacing={0}>
      <KnowledgeImportPanel canManage={canManage} />
      <KnowledgePage canManage={canManage} />
    </Stack>
  )
}
