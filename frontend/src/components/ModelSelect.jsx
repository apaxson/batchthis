import { useEffect, useMemo, useState } from 'react'
import Select from 'react-select'
import { cellarLedgerStyles } from './cellarLedgerStyles.js'

// Optional grouping: when the mount element carries data-prefer-type (e.g. the Log stage
// page's planned vessel type), options whose `vessel_type` matches are listed first under
// "Planned: <data-prefer-label>", the rest under data-other-label. The page can change
// the attribute at any time (a different stage chosen); the grouping follows it.
function readPreference(container) {
  if (!container) return { type: '', label: '', other: '' }
  return {
    type: container.dataset.preferType || '',
    label: container.dataset.preferLabel || container.dataset.preferType || '',
    other: container.dataset.otherLabel || 'Other',
  }
}

export default function ModelSelect({ endpoint, targetInput, placeholder, container }) {
  const [options, setOptions] = useState([])
  const [status, setStatus] = useState('loading')
  const [selected, setSelected] = useState(null)
  const [preference, setPreference] = useState(() => readPreference(container))

  useEffect(() => {
    if (!container) return undefined
    const observer = new MutationObserver(() => setPreference(readPreference(container)))
    observer.observe(container, { attributes: true, attributeFilter: ['data-prefer-type', 'data-prefer-label'] })
    return () => observer.disconnect()
  }, [container])

  const groupedOptions = useMemo(() => {
    if (!preference.type) return options
    const planned = options.filter((option) => option.type === preference.type)
    if (planned.length === 0) return options
    const others = options.filter((option) => option.type !== preference.type)
    const groups = [{ label: `Planned: ${preference.label}`, options: planned }]
    if (others.length) groups.push({ label: preference.other, options: others })
    return groups
  }, [options, preference])

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
          type: item.vessel_type,
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
      options={groupedOptions}
      value={selected}
      onChange={handleChange}
      placeholder={placeholder ?? 'Search...'}
      styles={cellarLedgerStyles}
    />
  )
}
