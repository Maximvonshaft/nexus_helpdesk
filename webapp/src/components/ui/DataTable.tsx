import { ReactNode } from 'react'

export function DataTable({ columns, rows }: { columns: string[]; rows: ReactNode[][] }) {
  return <table className="table"><thead><tr>{columns.map((col) => <th key={col}>{col}</th>)}</tr></thead><tbody>{rows.map((row, idx) => <tr key={idx}>{row.map((cell, cIdx) => <td key={cIdx}>{cell}</td>)}</tr>)}</tbody></table>
}
