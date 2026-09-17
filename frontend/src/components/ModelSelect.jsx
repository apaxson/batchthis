import { useEffect, useState } from 'react'
import Select from 'react-select'

// Matches the Cellar Ledger token palette (apps/batchthis/static/batchthis/css/
// cellar-ledger.css). Hex fallbacks mirror that file's :root defaults in case this
// component ever mounts on a page that doesn't define the custom properties.
const cellarLedgerStyles = {
  control: (base, state) => ({
    ...base,
    minHeight: 38,
    borderRadius: 3,
    borderColor: state.isFocused ? 'var(--focus, #2E5AAC)' : 'var(--line, #D3CFBC)',
    boxShadow: state.isFocused ? '0 0 0 2px var(--focus, #2E5AAC)' : 'none',
    backgroundColor: 'var(--paper-raised, #F5F6EF)',
    fontFamily: "'IBM Plex Sans', sans-serif",
    fontSize: '0.9rem',
    '&:hover': { borderColor: 'var(--ink-soft, #5B6154)' },
  }),
  menu: (base) => ({
    ...base,
    borderRadius: 3,
    border: '1px solid var(--line, #D3CFBC)',
    boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
    backgroundColor: 'var(--paper-raised, #F5F6EF)',
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
  singleValue: (base) => ({ ...base, color: 'var(--ink, #23291F)' }),
  placeholder: (base) => ({ ...base, color: 'var(--ink-faint, #8B9080)' }),
  input: (base) => ({ ...base, color: 'var(--ink, #23291F)', fontFamily: "'IBM Plex Sans', sans-serif" }),
  indicatorSeparator: (base) => ({ ...base, backgroundColor: 'var(--line, #D3CFBC)' }),
  dropdownIndicator: (base) => ({ ...base, color: 'var(--ink-soft, #5B6154)' }),
  clearIndicator: (base) => ({ ...base, color: 'var(--ink-soft, #5B6154)' }),
}

export default function ModelSelect({ endpoint, targetInput, placeholder }) {
  const [options, setOptions] = useState([])
  const [status, setStatus] = useState('loading')
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    let cancelled = false

    fetch(endpoint, { credentials: 'same-origin' })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Request failed with status ${response.status}`)
        }
        return response.json()
      })
      .then((data) => {
        if (cancelled) return
        const loadedOptions = data.map((item) => ({
          value: item.id,
          label: item.display_name,
        }))
        setOptions(loadedOptions)

        const currentValue = targetInput.value
        if (currentValue) {
          const match = loadedOptions.find((option) => String(option.value) === currentValue)
          setSelected(match ?? null)
        }
        setStatus('ready')
      })
      .catch((error) => {
        if (cancelled) return
        console.error(`ModelSelect: failed to load options from ${endpoint}`, error)
        setStatus('error')
      })

    return () => {
      cancelled = true
    }
  }, [endpoint, targetInput])

  const handleChange = (option) => {
    setSelected(option)
    targetInput.value = option ? option.value : ''
    targetInput.dispatchEvent(new Event('change', { bubbles: true }))
  }

  if (status === 'error') {
    return <div className="text-danger">Unable to load options. Please reload the page.</div>
  }

  return (
    <Select
      classNamePrefix="model-select"
      isClearable
      isLoading={status === 'loading'}
      isDisabled={status === 'loading'}
      options={options}
      value={selected}
      onChange={handleChange}
      placeholder={placeholder ?? 'Search...'}
      styles={cellarLedgerStyles}
    />
  )
}
