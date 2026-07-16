import {
  FormControl,
  FormHelperText,
  FormLabel,
  NativeSelect as MuiNativeSelect,
  OutlinedInput,
  Stack,
  Typography,
} from '@mui/material'
import { cloneElement, isValidElement, useId } from 'react'
import type { OutlinedInputProps } from '@mui/material/OutlinedInput'
import type { NativeSelectProps } from '@mui/material/NativeSelect'
import type { PropsWithChildren, ReactElement } from 'react'

export type FieldProps = PropsWithChildren<{
  label: string
  hint?: string
  description?: string
  example?: string
  error?: string
  required?: boolean
  disabledReason?: string
}>

export function Field({ label, children, hint, description, example, error, required, disabledReason }: FieldProps) {
  const generatedId = useId()
  const childProps = isValidElement(children) ? (children as ReactElement<Record<string, unknown>>).props : null
  const controlId = String(childProps?.id ?? generatedId)
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
    <FormControl fullWidth error={Boolean(error)} required={required}>
      <Stack spacing={0.75}>
        <FormLabel htmlFor={controlId} sx={{ color: 'text.primary', fontSize: 13, fontWeight: 650 }}>
          {label}
        </FormLabel>
        {description ? (
          <Typography id={`${generatedId}-description`} variant="caption" color="text.secondary">
            {description}
          </Typography>
        ) : null}
        {enhancedChildren}
        {hint ? <FormHelperText id={`${generatedId}-hint`}>{hint}</FormHelperText> : null}
        {example ? <FormHelperText id={`${generatedId}-example`}>示例：{example}</FormHelperText> : null}
        {disabledReason ? <FormHelperText id={`${generatedId}-disabled`}>当前不可用：{disabledReason}</FormHelperText> : null}
        {error ? <FormHelperText id={`${generatedId}-error`} role="alert">{error}</FormHelperText> : null}
      </Stack>
    </FormControl>
  )
}

export function Input(props: OutlinedInputProps) {
  return <OutlinedInput fullWidth {...props} />
}

export function Select(props: NativeSelectProps) {
  return <MuiNativeSelect fullWidth input={<OutlinedInput />} {...props} />
}

export function Textarea(props: OutlinedInputProps) {
  return <OutlinedInput fullWidth multiline minRows={3} {...props} />
}
