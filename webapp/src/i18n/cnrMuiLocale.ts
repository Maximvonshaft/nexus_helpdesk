const formatNumber = new Intl.NumberFormat('sr-Latn-ME').format

export const cnrMuiLocale = {
  components: {
    MuiBreadcrumbs: {
      defaultProps: {
        expandText: 'Prikaži putanju',
      },
    },
    MuiTablePagination: {
      defaultProps: {
        getItemAriaLabel: (type: 'first' | 'last' | 'next' | 'previous') => {
          if (type === 'first') return 'Idi na prvu stranicu'
          if (type === 'last') return 'Idi na posljednju stranicu'
          if (type === 'next') return 'Idi na sljedeću stranicu'
          return 'Idi na prethodnu stranicu'
        },
        labelRowsPerPage: 'Redova po stranici:',
        labelDisplayedRows: ({ from, to, count }: { from: number; to: number; count: number }) => (
          `${formatNumber(from)}–${formatNumber(to)} od ${count !== -1 ? formatNumber(count) : `više od ${formatNumber(to)}`}`
        ),
      },
    },
    MuiRating: {
      defaultProps: {
        getLabelText: (value: number) => {
          const lastDigit = value % 10
          const lastTwoDigits = value % 100
          if ([2, 3, 4].includes(lastDigit) && ![12, 13, 14].includes(lastTwoDigits)) return 'Zvijezde'
          return 'Zvijezda'
        },
        emptyLabelText: 'Prazno',
      },
    },
    MuiAutocomplete: {
      defaultProps: {
        clearText: 'Obriši',
        closeText: 'Zatvori',
        loadingText: 'Učitavanje…',
        noOptionsText: 'Nema opcija',
        openText: 'Otvori',
      },
    },
    MuiAlert: {
      defaultProps: {
        closeText: 'Zatvori',
      },
    },
    MuiPagination: {
      defaultProps: {
        'aria-label': 'Navigacija po stranicama',
        getItemAriaLabel: (type: 'page' | 'first' | 'last' | 'next' | 'previous' | 'start-ellipsis' | 'end-ellipsis', page: number | null, selected: boolean) => {
          if (type === 'page') return `${selected ? '' : 'Idi na '}stranicu ${page}`
          if (type === 'first') return 'Idi na prvu stranicu'
          if (type === 'last') return 'Idi na posljednju stranicu'
          if (type === 'next') return 'Idi na sljedeću stranicu'
          if (type === 'previous') return 'Idi na prethodnu stranicu'
          return 'Još stranica'
        },
      },
    },
  },
} as const
