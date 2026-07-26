import { alpha, createTheme } from '@mui/material/styles'

const systemFont = [
  'Inter',
  'ui-sans-serif',
  '-apple-system',
  'BlinkMacSystemFont',
  '"Segoe UI"',
  '"Noto Sans SC"',
  '"PingFang SC"',
  '"Microsoft YaHei"',
  'sans-serif',
].join(',')

const focusOutline = '3px solid #124AA8'

export const nexusTheme = createTheme({
  cssVariables: true,
  palette: {
    mode: 'light',
    primary: {
      main: '#175CD3',
      dark: '#124AA8',
      light: '#DCE8FF',
      contrastText: '#FFFFFF',
    },
    secondary: {
      main: '#344054',
      contrastText: '#FFFFFF',
    },
    success: {
      main: '#067647',
      dark: '#05603A',
      light: '#D1FADF',
    },
    warning: {
      main: '#B54708',
      dark: '#93370D',
      light: '#FEF0C7',
    },
    error: {
      main: '#B42318',
      dark: '#912018',
      light: '#FEE4E2',
    },
    info: {
      main: '#026AA2',
      dark: '#065986',
      light: '#D1E9FF',
    },
    background: {
      default: '#F7F8FA',
      paper: '#FFFFFF',
    },
    text: {
      primary: '#101828',
      secondary: '#475467',
      disabled: '#667085',
    },
    divider: '#E4E7EC',
    action: {
      hover: alpha('#175CD3', 0.06),
      selected: alpha('#175CD3', 0.1),
      disabledBackground: '#F2F4F7',
      disabled: '#667085',
    },
  },
  shape: {
    borderRadius: 8,
  },
  spacing: 8,
  typography: {
    fontFamily: systemFont,
    fontSize: 14,
    h1: { fontSize: '1.75rem', lineHeight: 1.2, fontWeight: 700, letterSpacing: '-0.02em' },
    h2: { fontSize: '1.375rem', lineHeight: 1.25, fontWeight: 700, letterSpacing: '-0.015em' },
    h3: { fontSize: '1.125rem', lineHeight: 1.3, fontWeight: 650 },
    h4: { fontSize: '1rem', lineHeight: 1.4, fontWeight: 650 },
    subtitle1: { fontSize: '0.9375rem', lineHeight: 1.45, fontWeight: 600 },
    subtitle2: { fontSize: '0.8125rem', lineHeight: 1.45, fontWeight: 600 },
    body1: { fontSize: '0.875rem', lineHeight: 1.55 },
    body2: { fontSize: '0.8125rem', lineHeight: 1.5 },
    button: { fontSize: '0.8125rem', lineHeight: 1.35, fontWeight: 650, textTransform: 'none' },
    caption: { fontSize: '0.75rem', lineHeight: 1.45 },
    overline: { fontSize: '0.6875rem', lineHeight: 1.4, fontWeight: 700, letterSpacing: '0.08em' },
  },
  transitions: {
    duration: {
      shortest: 120,
      shorter: 150,
      short: 180,
      standard: 200,
      complex: 220,
      enteringScreen: 180,
      leavingScreen: 150,
    },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        html: { minHeight: '100%', backgroundColor: '#F7F8FA' },
        body: {
          minHeight: '100%',
          margin: 0,
          backgroundColor: '#F7F8FA',
          color: '#101828',
          WebkitFontSmoothing: 'antialiased',
          MozOsxFontSmoothing: 'grayscale',
        },
        '#root': { minHeight: '100dvh' },
        '*': { boxSizing: 'border-box' },
        '::selection': { backgroundColor: alpha('#175CD3', 0.18) },
        'code, pre, kbd, samp': {
          fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
          fontVariantNumeric: 'tabular-nums',
        },
        '@media (prefers-reduced-motion: reduce)': {
          '*, *::before, *::after': {
            animationDuration: '0.01ms !important',
            animationIterationCount: '1 !important',
            scrollBehavior: 'auto !important',
            transitionDuration: '0.01ms !important',
          },
        },
        '@media (forced-colors: active)': {
          '*:focus-visible': {
            outlineColor: 'Highlight !important',
          },
        },
      },
    },
    MuiButtonBase: {
      defaultProps: {
        disableRipple: true,
      },
      styleOverrides: {
        root: {
          '&:focus-visible': {
            outline: focusOutline,
            outlineOffset: 2,
          },
        },
      },
    },
    MuiButton: {
      defaultProps: {
        disableElevation: true,
        size: 'medium',
      },
      styleOverrides: {
        root: {
          minHeight: 44,
          borderRadius: 8,
          paddingInline: 16,
          whiteSpace: 'normal',
          transition: 'background-color 150ms ease, border-color 150ms ease, color 150ms ease, box-shadow 150ms ease',
        },
        sizeSmall: { minHeight: 44, paddingInline: 12 },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: {
          minWidth: 44,
          minHeight: 44,
          borderRadius: 8,
        },
      },
    },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          minHeight: 44,
        },
      },
    },
    MuiCheckbox: {
      styleOverrides: {
        root: {
          minWidth: 44,
          minHeight: 44,
        },
      },
    },
    MuiRadio: {
      styleOverrides: {
        root: {
          minWidth: 44,
          minHeight: 44,
        },
      },
    },
    MuiSwitch: {
      styleOverrides: {
        root: {
          width: 64,
          minWidth: 64,
          height: 44,
          minHeight: 44,
          padding: 0,
        },
        switchBase: {
          width: 44,
          height: 44,
          minWidth: 44,
          minHeight: 44,
          padding: 12,
          '&.Mui-checked': {
            transform: 'translateX(20px)',
          },
        },
        input: {
          width: '100%',
          height: '100%',
        },
        track: {
          marginBlock: 8,
          marginInline: 6,
        },
      },
    },
    MuiTextField: {
      defaultProps: {
        size: 'small',
        fullWidth: true,
      },
    },
    MuiFormControl: {
      defaultProps: {
        size: 'small',
        fullWidth: true,
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          minHeight: 44,
          height: 'auto',
          borderRadius: 8,
          backgroundColor: '#FFFFFF',
          '& .MuiOutlinedInput-notchedOutline': { borderColor: '#D0D5DD' },
          '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: '#98A2B3' },
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': { borderWidth: 2 },
          '&.Mui-focused': { outline: focusOutline, outlineOffset: 2 },
        },
        input: {
          minHeight: 24,
          paddingTop: '1em',
          paddingBottom: '0.65em',
          boxSizing: 'border-box',
        },
        multiline: {
          paddingBlock: 8,
        },
      },
    },
    MuiSelect: {
      styleOverrides: {
        select: {
          alignItems: 'center',
          boxSizing: 'border-box',
          display: 'flex',
          minHeight: '44px !important',
          paddingTop: '1em !important',
          paddingBottom: '0.65em !important',
          whiteSpace: 'normal',
        },
      },
    },
    MuiChip: {
      defaultProps: {
        size: 'small',
        variant: 'outlined',
      },
      styleOverrides: {
        root: {
          minHeight: 26,
          height: 'auto',
          borderRadius: 6,
          fontWeight: 600,
          backgroundColor: '#FFFFFF',
        },
        label: {
          paddingBlock: 4,
          whiteSpace: 'normal',
          overflowWrap: 'anywhere',
        },
      },
    },
    MuiPaper: {
      defaultProps: {
        elevation: 0,
      },
      styleOverrides: {
        root: {
          backgroundImage: 'none',
        },
        outlined: {
          borderColor: '#E4E7EC',
        },
      },
    },
    MuiCard: {
      defaultProps: {
        elevation: 0,
        variant: 'outlined',
      },
      styleOverrides: {
        root: {
          borderColor: '#E4E7EC',
          borderRadius: 10,
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          alignItems: 'flex-start',
        },
      },
    },
    MuiDialog: {
      defaultProps: {
        fullWidth: true,
        maxWidth: 'sm',
      },
    },
    MuiDialogTitle: {
      styleOverrides: {
        root: { fontSize: '1.125rem', fontWeight: 700, padding: '24px 24px 8px' },
      },
    },
    MuiDialogContent: {
      styleOverrides: {
        root: { padding: '8px 24px 16px' },
      },
    },
    MuiDialogActions: {
      styleOverrides: {
        root: { padding: '12px 24px 24px', gap: 8, flexWrap: 'wrap' },
      },
    },
    MuiTooltip: {
      defaultProps: {
        arrow: true,
        enterDelay: 450,
      },
      styleOverrides: {
        tooltip: { fontSize: '0.75rem', borderRadius: 6, padding: '8px 10px' },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: {
          borderBottomColor: '#EAECF0',
          paddingBlock: 10,
          fontVariantNumeric: 'tabular-nums',
        },
        head: {
          backgroundColor: '#F9FAFB',
          color: '#475467',
          fontWeight: 650,
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: {
          minHeight: 44,
          textTransform: 'none',
          fontWeight: 650,
        },
      },
    },
    MuiAccordionSummary: {
      styleOverrides: {
        root: {
          minHeight: 44,
        },
      },
    },
  },
})
