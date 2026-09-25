import { useEffect, useState } from 'react'
import CreatableSelect from 'react-select/creatable'
import { cellarLedgerStyles } from './cellarLedgerStyles.js'

// Keep in step with PairingTag.NAME_MAX_LENGTH (models.py); the server checks it too.
const MAX_NAME_LENGTH = 50

// Chips match the .cl-tag labels on the recipe page (cellar-ledger.css).
const tagStyles = {
  ...cellarLedgerStyles,
  multiValue: (base) => ({
    ...base,
    backgroundColor: 'var(--paper, #E9ECE0)',
    border: '1px solid var(--line, #D3CFBC)',
    borderRadius: 3,
  }),
  multiValueLabel: (base) => ({
    ...base,
    color: 'var(--ink, #23291F)',
    fontSize: '0.82rem',
    fontWeight: 500,
  }),
  multiValueRemove: (base) => ({
    ...base,
    color: 'var(--ink-soft, #5B6154)',
    ':hover': { backgroundColor: 'transparent', color: 'var(--brick, #96382B)' },
  }),
}

// Same rules as forms.PairingTagsField: split on commas, trim and collapse spaces,
// drop blanks and case-insensitive duplicates (first spelling kept).
function splitNames(text) {
  const seen = new Set()
  return (text || '')
    .split(',')
    .map((part) => part.split(/\s+/).filter(Boolean).join(' '))
    .filter((name) => {
      const key = name.toLowerCase()
      if (!name || seen.has(key)) return false
      seen.add(key)
      return true
    })
}

const toOption = (name) => ({ value: name, label: name })

// A searchable multi-select of tags that can also create new ones. It writes the chosen
// names back into `targetInput` (the form's text field) as "Roast chicken, Aged cheddar",
// so the server sees the same thing it gets without JavaScript.
export default function TagSelect({ endpoint, targetInput, placeholder, label }) {
  const [options, setOptions] = useState([])
  const [status, setStatus] = useState('loading')
  const [selected, setSelected] = useState(() => splitNames(targetInput.value).map(toOption))

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
        setOptions(data.map((tag) => toOption(tag.name)))
        setStatus('ready')
      })
      .catch((error) => {
        if (cancelled) return
        console.error(`TagSelect: failed to load tags from ${endpoint}`, error)
        setStatus('error')
      })

    return () => {
      cancelled = true
    }
  }, [endpoint])

  // The plain text field stays the fallback: shown again if the tags can't load.
  useEffect(() => {
    targetInput.hidden = status !== 'error'
  }, [status, targetInput])

  const write = (values) => {
    setSelected(values)
    targetInput.value = values.map((option) => option.label).join(', ')
    targetInput.dispatchEvent(new Event('change', { bubbles: true }))
  }

  const handleChange = (values) => {
    write(splitNames((values || []).map((option) => option.label).join(',')).map(toOption))
  }

  // Typing "Brie, Fig jam" and pressing Enter adds both.
  const handleCreate = (input) => {
    const names = splitNames([...selected.map((option) => option.label), input].join(','))
    write(names.map(toOption))
    const known = new Set(options.map((option) => option.label.toLowerCase()))
    setOptions([...options, ...names.filter((name) => !known.has(name.toLowerCase())).map(toOption)])
  }

  const isValidNewOption = (input) => {
    const names = splitNames(input)
    if (names.length === 0 || names.some((name) => name.length > MAX_NAME_LENGTH)) return false
    const taken = new Set([...options, ...selected].map((option) => option.label.toLowerCase()))
    return names.some((name) => !taken.has(name.toLowerCase()))
  }

  if (status === 'error') {
    return <div className="cl-field-error">Couldn&apos;t load earlier pairings. Type them above, separated by commas.</div>
  }

  return (
    <CreatableSelect
      classNamePrefix="tag-select"
      isMulti
      isClearable={false}
      isLoading={status === 'loading'}
      options={options}
      value={selected}
      onChange={handleChange}
      onCreateOption={handleCreate}
      isValidNewOption={isValidNewOption}
      formatCreateLabel={(input) => `Add "${splitNames(input).join(', ')}"`}
      noOptionsMessage={() => 'Type a pairing and press Enter'}
      placeholder={placeholder ?? 'Pick or type a pairing...'}
      aria-label={label}
      styles={tagStyles}
    />
  )
}
