import { Button } from '@/components/ui/Button'

export type WebCallQueueFilterTab<Key extends string = string> = {
  key: Key
  label: string
}

type WebCallQueueFiltersProps<Key extends string = string> = {
  tabs: readonly WebCallQueueFilterTab<Key>[]
  activeKey: Key
  onSelect: (key: Key) => void
  ariaLabel?: string
}

export function WebCallQueueFilters<Key extends string>({
  tabs,
  activeKey,
  onSelect,
  ariaLabel = 'WebCall Operational Queue filters',
}: WebCallQueueFiltersProps<Key>) {
  return (
    <div className="inline-actions" role="group" aria-label={ariaLabel}>
      {tabs.map((tab) => {
        const active = activeKey === tab.key
        return (
          <Button
            key={tab.key}
            variant={active ? 'primary' : 'secondary'}
            aria-pressed={active}
            onClick={() => onSelect(tab.key)}
          >
            {tab.label}
          </Button>
        )
      })}
    </div>
  )
}
