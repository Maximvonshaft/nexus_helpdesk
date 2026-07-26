import { createContext, useContext } from 'react'

export interface OperatorLayoutMode {
  desktopLayout: boolean
  textScale: number
}

export const OperatorLayoutContext = createContext<OperatorLayoutMode | null>(null)

export function useOperatorLayoutMode() {
  const value = useContext(OperatorLayoutContext)
  if (!value) throw new Error('useOperatorLayoutMode must be used inside OperatorLayoutProvider')
  return value
}
