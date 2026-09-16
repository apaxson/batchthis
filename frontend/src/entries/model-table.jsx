import { createRoot } from 'react-dom/client'
import ModelTable from '../components/ModelTable.jsx'

const MOUNTED_ATTR = 'data-model-table-mounted'

function mountModelTables(root = document) {
  const containers = root.querySelectorAll(`[data-react-model-table]:not([${MOUNTED_ATTR}])`)

  containers.forEach((container) => {
    const endpoint = container.dataset.endpoint
    let columns = []
    try {
      columns = JSON.parse(container.dataset.columns || '[]')
    } catch (error) {
      console.error('ModelTable: invalid data-columns JSON', container, error)
      return
    }

    if (!endpoint || columns.length === 0) {
      console.error('ModelTable: missing data-endpoint or data-columns', container)
      return
    }

    container.setAttribute(MOUNTED_ATTR, 'true')
    createRoot(container).render(
      <ModelTable
        endpoint={endpoint}
        columns={columns}
        searchPlaceholder={container.dataset.searchPlaceholder}
        emptyMessage={container.dataset.emptyMessage}
      />
    )
  })
}

window.BatchThis = window.BatchThis || {}
window.BatchThis.mountModelTables = mountModelTables

document.addEventListener('DOMContentLoaded', () => mountModelTables())
