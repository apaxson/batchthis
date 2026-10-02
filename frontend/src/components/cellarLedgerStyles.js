// Matches the Cellar Ledger token palette (apps/batchthis/static/batchthis/css/
// cellar-ledger.css). Hex fallbacks mirror that file's :root defaults in case this
// component ever mounts on a page that doesn't define the custom properties.
export const cellarLedgerStyles = {
  control: (base, state) => ({
    ...base,
    minHeight: 38,
    borderRadius: 10,
    borderColor: state.isFocused ? 'var(--focus, #2E5AAC)' : 'var(--line, #DFE0D4)',
    boxShadow: state.isFocused ? '0 0 0 3px color-mix(in srgb, var(--focus, #2E5AAC) 22%, transparent)' : 'none',
    backgroundColor: '#fff',
    fontFamily: "var(--font-sans, 'Inter', sans-serif)",
    fontSize: '0.9rem',
    '&:hover': { borderColor: 'var(--ink-soft, #5B6154)' },
  }),
  menu: (base) => ({
    ...base,
    borderRadius: 12,
    border: 'none',
    boxShadow: 'var(--shadow-pop, 0 12px 32px -8px rgba(31,36,25,0.22))',
    backgroundColor: 'var(--paper-raised, #FBFBF7)',
    overflow: 'hidden',
    zIndex: 20,
  }),
  option: (base, state) => ({
    ...base,
    backgroundColor: state.isSelected
      ? 'var(--honey-line, #B87A1F)'
      : state.isFocused
        ? 'var(--paper, #E9ECE0)'
        : 'transparent',
    color: state.isSelected ? 'var(--paper-raised, #F5F6EF)' : 'var(--ink, #23291F)',
    fontSize: '0.9rem',
    cursor: 'pointer',
  }),
  // Group headings (e.g. "Planned: any Barrel") read like the app's field labels, not all caps.
  groupHeading: (base) => ({
    ...base,
    textTransform: 'none',
    fontSize: '0.72rem',
    fontWeight: 600,
    letterSpacing: '0.02em',
    color: 'var(--ink-soft, #5B6154)',
  }),
  singleValue: (base) => ({ ...base, color: 'var(--ink, #23291F)' }),
  placeholder: (base) => ({ ...base, color: 'var(--ink-faint, #8B9080)' }),
  input: (base) => ({ ...base, color: 'var(--ink, #23291F)', fontFamily: "var(--font-sans, 'Inter', sans-serif)" }),
  indicatorSeparator: (base) => ({ ...base, backgroundColor: 'var(--line, #D3CFBC)' }),
  dropdownIndicator: (base) => ({ ...base, color: 'var(--ink-soft, #5B6154)' }),
  clearIndicator: (base) => ({ ...base, color: 'var(--ink-soft, #5B6154)' }),
}
