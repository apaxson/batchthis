import { createRoot } from 'react-dom/client'
import ModelSelect from '../components/ModelSelect.jsx'

const MOUNTED_ATTR = 'data-model-select-mounted'

function mountModelSelects(root = document) {
  const containers = root.querySelectorAll(`[data-react-model-select]:not([${MOUNTED_ATTR}])`)

  containers.forEach((container) => {
    const endpoint = container.dataset.endpoint
    const targetSelector = container.dataset.target
    const targetInput = document.querySelector(targetSelector)

    if (!endpoint || !targetInput) {
      console.error('ModelSelect: missing data-endpoint or data-target', container)
      return
    }

    container.setAttribute(MOUNTED_ATTR, 'true')
    createRoot(container).render(
      <ModelSelect
        endpoint={endpoint}
        targetInput={targetInput}
        placeholder={container.dataset.placeholder}
      />
    )
  })
}

window.BatchThis = window.BatchThis || {}
window.BatchThis.mountModelSelects = mountModelSelects

document.addEventListener('DOMContentLoaded', () => mountModelSelects())
