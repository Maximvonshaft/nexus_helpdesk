import { ReactNode } from 'react'
import { EmptyState } from './EmptyState'

export function DataTable({
  columns,
  rows,
  caption,
  empty,
  loading,
}: {
  columns: string[]
  rows: ReactNode[][]
  caption?: string
  empty?: ReactNode
  loading?: boolean
}) {
  return (
    <table className="table">
      {caption ? <caption>{caption}</caption> : null}
      <thead><tr>{columns.map((col) => <th key={col}>{col}</th>)}</tr></thead>
      <tbody>
        {loading ? <tr><td colSpan={columns.length}>正在加载…</td></tr> : null}
        {!loading && rows.map((row, idx) => <tr key={idx}>{row.map((cell, cIdx) => <td key={cIdx}>{cell}</td>)}</tr>)}
        {!loading && !rows.length ? <tr><td colSpan={columns.length}>{empty ?? <EmptyState text="暂无数据。" />}</td></tr> : null}
      </tbody>
    </table>
  )
}
