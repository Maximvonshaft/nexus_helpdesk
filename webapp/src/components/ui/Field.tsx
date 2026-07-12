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
  className?: string
}>

export function Field({ label, children, hint, description, example, error, required, disabledReason, className }: FieldProps) {
  const generatedId = useId()
  const childProps = isValidElement(children) ? (children as ReactElement<Record<string, unknown>>).props : null
  const controlId = typeof childProps?.id === 'string' ? childProps.id : generatedId
  const describedBy = [
    description ? `${generatedId}-description` : null,
    hint ? `${generatedId}-hint` : null,
    example ? `${generatedId}-example` : null,
    disabledReason ? `${generatedId}-disabled` : null,
    error ? `${generatedId}-error` : null,
  ].filter(Boolean).join(' ') || undefined

  const enhancedChildren = childProps
    ? cloneElement(children as ReactElement<Record<string, unknown>>, {
      id: controlId,
      'aria-invalid': error ? true : childProps['aria-invalid'],
      'aria-describedby': [childProps['aria-describedby'], describedBy].filter(Boolean).join(' ') || undefined,
      required: required ?? childProps.required,
    })
    : children

  return (
    <div className={cn('field', 'nd-field', className)}>
      <label className="field-label nd-field__label" htmlFor={controlId}>
        {label}{required ? <span className="field-required nd-field__required"> 必填</span> : null}
      </label>
      {description ? <span id={`${generatedId}-description`} className="field-description nd-field__description">{description}</span> : null}
      {enhancedChildren}
      {hint ? <span id={`${generatedId}-hint`} className="field-hint nd-field__hint">{hint}</span> : null}
      {example ? <span id={`${generatedId}-example`} className="field-example nd-field__example">示例：{example}</span> : null}
      {disabledReason ? <span id={`${generatedId}-disabled`} className="field-hint nd-field__disabled">当前不可用：{disabledReason}</span> : null}
      {error ? <span id={`${generatedId}-error`} className="field-error nd-field__error" role="alert">{error}</span> : null}
    </div>
  )
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn('input', 'nd-field-control', 'nd-input', className)} />
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cn('select', 'nd-field-control', 'nd-select', className)} />
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cn('textarea', 'nd-field-control', 'nd-textarea', className)} />
}
