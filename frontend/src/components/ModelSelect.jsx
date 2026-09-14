import { useEffect, useState } from 'react'
import Select from 'react-select'

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
    />
  )
}
