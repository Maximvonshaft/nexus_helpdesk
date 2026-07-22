import AddLocationAltRoundedIcon from '@mui/icons-material/AddLocationAltRounded'
import SaveRoundedIcon from '@mui/icons-material/SaveRounded'
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { OperatorEmptyState, OperatorErrorNotice } from '@/app/OperatorPresentation'
import { governanceApi, type GovernedMarket, type MarketDraft } from '@/lib/governanceApi'

const EMPTY_MARKET: MarketDraft = {
  code: '',
  name: '',
  timezone: 'Europe/Podgorica',
  status: 'draft',
  default_currency: 'EUR',
  owner_team_id: null,
  data_region: 'eu',
  notes: '',
  country_codes: [],
  language_codes: ['en'],
}

export function MarketGovernancePanel() {
  const queryClient = useQueryClient()
  const markets = useQuery({ queryKey: ['governance', 'markets'], queryFn: governanceApi.markets })
  const countries = useQuery({ queryKey: ['governance', 'countries'], queryFn: governanceApi.countries })
  const teams = useQuery({ queryKey: ['governance', 'market-teams'], queryFn: governanceApi.marketTeams })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [draft, setDraft] = useState<MarketDraft>(EMPTY_MARKET)
  const [createOpen, setCreateOpen] = useState(false)
  const selected = useMemo(() => markets.data?.find((item) => item.id === selectedId) ?? null, [markets.data, selectedId])

  useEffect(() => {
    if (!selected && markets.data?.length) setSelectedId(markets.data[0].id)
  }, [markets.data, selected])
  useEffect(() => {
    if (!selected) return
    setDraft({
      code: selected.code,
      name: selected.name,
      timezone: selected.timezone || '',
      status: selected.status,
      default_currency: selected.default_currency || '',
      owner_team_id: selected.owner_team_id || null,
      data_region: selected.data_region || '',
      notes: selected.notes || '',
      country_codes: selected.countries,
      language_codes: selected.languages,
    })
  }, [selected])

  const invalidate = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['governance', 'markets'] }),
    queryClient.invalidateQueries({ queryKey: ['markets'] }),
  ])
  const create = useMutation({
    mutationFn: governanceApi.createMarket,
    onSuccess: async (row) => { setSelectedId(row.id); setCreateOpen(false); await invalidate() },
  })
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<MarketDraft> & { expected_version: number } }) => governanceApi.updateMarket(id, payload),
    onSuccess: invalidate,
  })

  if (markets.isLoading || countries.isLoading || teams.isLoading) return <Stack sx={{ alignItems: 'center', py: 6 }}><CircularProgress /></Stack>
  const error = markets.error || countries.error || teams.error
  if (error) return <OperatorErrorNotice title="无法读取市场治理配置" error={error} fallback="请稍后重试" />

  const countryOptions = countries.data || []
  const save = () => {
    if (!selected) return
    update.mutate({
      id: selected.id,
      payload: {
        name: draft.name,
        timezone: draft.timezone || null,
        status: draft.status,
        default_currency: draft.default_currency || null,
        owner_team_id: draft.owner_team_id || null,
        data_region: draft.data_region || null,
        notes: draft.notes || null,
        country_codes: draft.country_codes,
        language_codes: draft.language_codes,
        expected_version: selected.version,
      },
    })
  }

  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between' }}>
          <Box>
            <Typography component="h2" variant="h2">市场与国家</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              市场继续使用现有 Market 权威；本页只补充国家目录、语言、生命周期、责任团队和退役影响检查。
            </Typography>
          </Box>
          <Button variant="contained" startIcon={<AddLocationAltRoundedIcon />} onClick={() => setCreateOpen(true)}>新建市场</Button>
        </Stack>
      </Paper>

      {!markets.data?.length ? (
        <OperatorEmptyState title="暂无经营市场" description="从受控国家目录创建第一个市场。" />
      ) : (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: 'minmax(260px, 0.8fr) minmax(0, 2fr)' }, gap: 2 }}>
          <Paper variant="outlined" sx={{ p: 1.5 }}>
            <Stack spacing={1}>
              {markets.data.map((item) => (
                <Button key={item.id} variant={item.id === selectedId ? 'contained' : 'text'} color={item.id === selectedId ? 'primary' : 'inherit'} onClick={() => setSelectedId(item.id)} sx={{ justifyContent: 'space-between' }}>
                  <span>{item.name}</span>
                  <Chip size="small" color={item.status === 'active' ? 'success' : item.status === 'retired' ? 'default' : 'warning'} label={item.status} />
                </Button>
              ))}
            </Stack>
          </Paper>

          {selected ? (
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Stack spacing={2}>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ justifyContent: 'space-between' }}>
                  <Box>
                    <Typography variant="h3">{selected.name}</Typography>
                    <Typography variant="caption" color="text.secondary">{selected.code} · 配置版本 {selected.version} · {selected.countries.join(', ')}</Typography>
                  </Box>
                  <Chip color={selected.status === 'active' ? 'success' : 'warning'} label={selected.status} />
                </Stack>
                {selected.status === 'retired' ? <Alert severity="info">该市场已经退役。重新激活会恢复 Market 运行状态，但不会自动恢复依赖资源。</Alert> : null}
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                  <TextField fullWidth label="市场名称" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
                  <TextField fullWidth label="市场代码" value={draft.code} disabled />
                </Stack>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                  <TextField select fullWidth label="生命周期" value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value as GovernedMarket['status'] })}>
                    {['draft', 'active', 'paused', 'retiring', 'retired'].map((status) => <MenuItem key={status} value={status}>{status}</MenuItem>)}
                  </TextField>
                  <TextField fullWidth label="IANA 时区" value={draft.timezone || ''} onChange={(event) => setDraft({ ...draft, timezone: event.target.value })} />
                  <TextField fullWidth label="默认币种" value={draft.default_currency || ''} onChange={(event) => setDraft({ ...draft, default_currency: event.target.value.toUpperCase().slice(0, 3) })} />
                </Stack>
                <Autocomplete
                  multiple
                  options={countryOptions}
                  getOptionLabel={(option) => `${option.canonical_name} (${option.iso_alpha2})`}
                  value={countryOptions.filter((option) => draft.country_codes.includes(option.iso_alpha2))}
                  onChange={(_, value) => setDraft({ ...draft, country_codes: value.map((item) => item.iso_alpha2) })}
                  renderInput={(params) => <TextField {...params} label="国家范围" helperText="列表第一项作为主国家。" />}
                />
                <Autocomplete
                  multiple
                  freeSolo
                  options={['en', 'de', 'fr', 'it', 'pt', 'es', 'zh', 'me', 'sr', 'mk', 'uk']}
                  value={draft.language_codes}
                  onChange={(_, value) => setDraft({ ...draft, language_codes: value.map(String) })}
                  renderInput={(params) => <TextField {...params} label="语言代码" helperText="使用 BCP 47 语言代码，第一项作为主语言。" />}
                />
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                  <TextField select fullWidth label="责任团队" value={draft.owner_team_id || ''} onChange={(event) => setDraft({ ...draft, owner_team_id: event.target.value ? Number(event.target.value) : null })}>
                    <MenuItem value="">未指定</MenuItem>
                    {(teams.data || []).map((team) => <MenuItem key={team.id} value={team.id}>{team.name}</MenuItem>)}
                  </TextField>
                  <TextField fullWidth label="数据区域" value={draft.data_region || ''} onChange={(event) => setDraft({ ...draft, data_region: event.target.value })} />
                </Stack>
                <TextField label="运营备注" multiline minRows={2} value={draft.notes || ''} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} />
                <Box>
                  <Typography variant="subtitle2">退役影响</Typography>
                  <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', mt: 1 }}>
                    {Object.entries(selected.impact).map(([key, value]) => <Chip key={key} color={value ? 'warning' : 'default'} label={`${key}: ${value}`} />)}
                  </Stack>
                </Box>
                <Button variant="contained" startIcon={<SaveRoundedIcon />} disabled={update.isPending || !draft.name.trim() || !draft.country_codes.length || !draft.language_codes.length} onClick={save}>保存市场配置</Button>
                {update.error ? <OperatorErrorNotice title="市场更新失败" error={update.error} fallback="请刷新后重试；退役前必须先解除所有活动依赖" /> : null}
              </Stack>
            </Paper>
          ) : null}
        </Box>
      )}

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} fullWidth maxWidth="md">
        <MarketEditor countries={countryOptions} teams={teams.data || []} pending={create.isPending} error={create.error} onClose={() => setCreateOpen(false)} onSubmit={(payload) => create.mutate(payload)} />
      </Dialog>
    </Stack>
  )
}

function MarketEditor({ countries, teams, pending, error, onClose, onSubmit }: {
  countries: Awaited<ReturnType<typeof governanceApi.countries>>
  teams: Awaited<ReturnType<typeof governanceApi.marketTeams>>
  pending: boolean
  error: unknown
  onClose: () => void
  onSubmit: (payload: MarketDraft) => void
}) {
  const [draft, setDraft] = useState<MarketDraft>(EMPTY_MARKET)
  return (
    <>
      <DialogTitle>新建经营市场</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
            <TextField fullWidth label="市场代码" value={draft.code} onChange={(event) => setDraft({ ...draft, code: event.target.value.toUpperCase().slice(0, 16) })} />
            <TextField fullWidth label="市场名称" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
          </Stack>
          <Autocomplete multiple options={countries} getOptionLabel={(option) => `${option.canonical_name} (${option.iso_alpha2})`} value={countries.filter((option) => draft.country_codes.includes(option.iso_alpha2))} onChange={(_, value) => setDraft({ ...draft, country_codes: value.map((item) => item.iso_alpha2) })} renderInput={(params) => <TextField {...params} label="国家范围" />} />
          <Autocomplete multiple freeSolo options={['en', 'de', 'fr', 'it', 'pt', 'es', 'zh', 'me', 'sr', 'mk', 'uk']} value={draft.language_codes} onChange={(_, value) => setDraft({ ...draft, language_codes: value.map(String) })} renderInput={(params) => <TextField {...params} label="语言代码" />} />
          <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
            <TextField fullWidth label="IANA 时区" value={draft.timezone || ''} onChange={(event) => setDraft({ ...draft, timezone: event.target.value })} />
            <TextField fullWidth label="默认币种" value={draft.default_currency || ''} onChange={(event) => setDraft({ ...draft, default_currency: event.target.value.toUpperCase().slice(0, 3) })} />
          </Stack>
          <TextField select label="责任团队" value={draft.owner_team_id || ''} onChange={(event) => setDraft({ ...draft, owner_team_id: event.target.value ? Number(event.target.value) : null })}>
            <MenuItem value="">未指定</MenuItem>
            {teams.map((team) => <MenuItem key={team.id} value={team.id}>{team.name}</MenuItem>)}
          </TextField>
          <TextField label="数据区域" value={draft.data_region || ''} onChange={(event) => setDraft({ ...draft, data_region: event.target.value })} />
          <TextField label="运营备注" multiline minRows={2} value={draft.notes || ''} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} />
          {error ? <OperatorErrorNotice title="创建市场失败" error={error} fallback="请检查代码、国家、语言和时区" /> : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>取消</Button>
        <Button variant="contained" disabled={pending || !draft.code.trim() || !draft.name.trim() || !draft.country_codes.length || !draft.language_codes.length} onClick={() => onSubmit(draft)}>创建</Button>
      </DialogActions>
    </>
  )
}
