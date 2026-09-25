import { createRoot } from 'react-dom/client'
import TagSelect from '../components/TagSelect.jsx'

const MOUNTED_ATTR = 'data-tag-select-mounted'

// Mounts a TagSelect in each [data-react-tag-select] element. data-target is the form's
// text field it writes to; data-endpoint lists the existing tags ([{id, name}]).
function mountTagSelects(root = document) {
  const containers = root.querySelectorAll(`[data-react-tag-select]:not([${MOUNTED_ATTR}])`)

  containers.forEach((container) => {
    const endpoint = container.dataset.endpoint
    const targetInput = document.querySelector(container.dataset.target)

    if (!endpoint || !targetInput) {
      console.error('TagSelect: missing data-endpoint or data-target', container)
      return
    }

    container.setAttribute(MOUNTED_ATTR, 'true')
    targetInput.hidden = true
    createRoot(container).render(
      <TagSelect
        endpoint={endpoint}
        targetInput={targetInput}
        placeholder={container.dataset.placeholder}
        label={container.dataset.label}
      />
    )
  })
}

window.BatchThis = window.BatchThis || {}
window.BatchThis.mountTagSelects = mountTagSelects

document.addEventListener('DOMContentLoaded', () => mountTagSelects())
