import { cloneElement, isValidElement, useId } from 'react'
import type { InputHTMLAttributes, PropsWithChildren, ReactElement, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

type FieldProps = PropsWithChildren<{
  label: string
  hint?: string
  description?: string
  example?: string
  error?: string
  required?: boolean
  disabledReason?: string
}>

export function Field({ label, children, hint, description, example, error, required, disabledReason }: FieldProps) {
  const id = useId()
  const describedBy = [
    description ? `${id}-description` : null,
    hint ? `${id}-hint` : null,
    example ? `${id}-example` : null,
    disabledReason ? `${id}-disabled` : null,
    error ? `${id}-error` : null,
  ].filter(Boolean).join(' ') || undefined
  const childProps = isValidElement(children) ? (children as ReactElement<Record<string, unknown>>).props : null
  const enhancedChildren = childProps
    ? cloneElement(children as ReactElement<Record<string, unknown>>, {
      id: childProps.id ?? id,
      'aria-invalid': error ? true : childProps['aria-invalid'],
      'aria-describedby': [childProps['aria-describedby'], describedBy].filter(Boolean).join(' ') || undefined,
      required: required ?? childProps.required,
    })
    : children

  return (
    <label className="field">
      <span className="field-label">{label}{required ? <span className="field-required"> 必填</span> : null}</span>
      {description ? <span id={`${id}-description`} className="field-description">{description}</span> : null}
      {enhancedChildren}
      {hint ? <span id={`${id}-hint`} className="field-hint">{hint}</span> : null}
      {example ? <span id={`${id}-example`} className="field-example">示例：{example}</span> : null}
      {disabledReason ? <span id={`${id}-disabled`} className="field-hint">当前不可用：{disabledReason}</span> : null}
      {error ? <span id={`${id}-error`} className="field-error" role="alert">{error}</span> : null}
    </label>
  )
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn('input', props.className)} {...props} />
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn('select', props.className)} {...props} />
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn('textarea', props.className)} {...props} />
}
