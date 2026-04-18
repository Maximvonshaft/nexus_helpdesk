import { ButtonHTMLAttributes } from 'react'

export function SegmentedControl({
  options,
  value,
  onChange,
}: {
  options: Array<{ label: string; value: string }>
  value: string
  onChange: (next: string) => void
}) {
  return (
    <div className="segmented-control" role="tablist">
      {options.map((item) => (
        <button
          key={item.value}
          type="button"
          className="segmented-option"
          data-active={value === item.value ? 'true' : 'false'}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

export function ToolbarAction(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" className="toolbar-action" {...props} />
}
