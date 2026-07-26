import { Box, useMediaQuery } from '@mui/material'
import { useTheme } from '@mui/material/styles'
import {
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { OperatorLayoutContext, type OperatorLayoutMode } from './useOperatorLayoutMode'

function initialTextScale() {
  if (typeof window === 'undefined') return 1
  const rootSize = Number.parseFloat(window.getComputedStyle(document.documentElement).fontSize)
  return Number.isFinite(rootSize) && rootSize > 0 ? rootSize / 16 : 1
}

export function OperatorLayoutProvider({ children }: { children: ReactNode }) {
  const theme = useTheme()
  const wideViewport = useMediaQuery(theme.breakpoints.up('lg'), { noSsr: true })
  const probeRef = useRef<HTMLSpanElement | null>(null)
  const [textScale, setTextScale] = useState(initialTextScale)

  useLayoutEffect(() => {
    const probe = probeRef.current
    if (!probe) return undefined

    const measure = () => {
      const remWidth = probe.getBoundingClientRect().width
      const next = Number.isFinite(remWidth) && remWidth > 0 ? remWidth / 16 : initialTextScale()
      setTextScale((current) => (Math.abs(current - next) < 0.01 ? current : next))
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(probe)
    observer.observe(document.documentElement)
    window.addEventListener('resize', measure)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [])

  const value = useMemo<OperatorLayoutMode>(() => ({
    // At 150% text enlargement the fixed-density desktop shell no longer has
    // enough physical room for navigation, live controls and task columns.
    desktopLayout: wideViewport && textScale < 1.5,
    textScale,
  }), [textScale, wideViewport])

  return (
    <OperatorLayoutContext.Provider value={value}>
      <Box
        ref={probeRef}
        component="span"
        aria-hidden="true"
        sx={{
          position: 'fixed',
          width: '1rem',
          height: 1,
          left: -10_000,
          top: 0,
          visibility: 'hidden',
          pointerEvents: 'none',
        }}
      />
      {children}
    </OperatorLayoutContext.Provider>
  )
}
