import { useEffect, useMemo, useState } from 'react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
} from '@tanstack/react-table'

// Generic sortable/searchable table for a Model listing endpoint (an
// api/<model>s/ DRF view returning a flat JSON array). `columns` describes
// which fields to show - see src/entries/model-table.jsx for the
// data-columns contract read off the mount point.
export default function ModelTable({ endpoint, columns: columnConfig, searchPlaceholder, emptyMessage }) {
  const [data, setData] = useState([])
  const [status, setStatus] = useState('loading')
  const [sorting, setSorting] = useState([])
  const [globalFilter, setGlobalFilter] = useState('')

  useEffect(() => {
    let cancelled = false

    fetch(endpoint, { credentials: 'same-origin' })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Request failed with status ${response.status}`)
        }
        return response.json()
      })
      .then((rows) => {
        if (cancelled) return
        setData(rows)
        setStatus('ready')
      })
      .catch((error) => {
        if (cancelled) return
        console.error(`ModelTable: failed to load rows from ${endpoint}`, error)
        setStatus('error')
      })

    return () => {
      cancelled = true
    }
  }, [endpoint])

  const columns = useMemo(
    () =>
      columnConfig.map((col) => ({
        accessorKey: col.key,
        header: col.label,
        enableSorting: col.sortable !== false,
        enableGlobalFilter: col.filterable !== false,
        cell: (info) => {
          const value = info.getValue()
          const display = value === null || value === undefined || value === '' ? '—' : `${value}${col.suffix || ''}`
          if (col.linkField) {
            const href = info.row.original[col.linkField]
            return href ? (
              <a href={href} className="cl-row-link">
                {display}
              </a>
            ) : (
              display
            )
          }
          return display
        },
      })),
    [columnConfig]
  )

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  })

  if (status === 'error') {
    return <div className="cl-empty">Unable to load data. Please reload the page.</div>
  }

  const rows = table.getRowModel().rows

  return (
    <div className="cl-model-table">
      <div className="cl-table-toolbar">
        <input
          type="search"
          className="cl-table-search"
          placeholder={searchPlaceholder || 'Search...'}
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          aria-label="Filter table"
        />
      </div>
      <table className="cl-ledger">
        <thead>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const sortDir = header.column.getIsSorted()
                return (
                  <th
                    key={header.id}
                    className={header.column.getCanSort() ? 'cl-th-sortable' : ''}
                    onClick={header.column.getToggleSortingHandler()}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {header.column.getCanSort() && (
                      <span className="cl-sort-indicator">
                        {sortDir === 'asc' ? ' ↑' : sortDir === 'desc' ? ' ↓' : ''}
                      </span>
                    )}
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {status === 'loading' ? (
            <tr>
              <td className="cl-empty" colSpan={columns.length}>
                Loading...
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td className="cl-empty" colSpan={columns.length}>
                {emptyMessage || 'No results.'}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
