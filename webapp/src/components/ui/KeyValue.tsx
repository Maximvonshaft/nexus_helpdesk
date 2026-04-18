export function KeyValue({ label, value }: { label: string; value?: string | number | null }) {
  return <div className="kv"><label>{label}</label><div>{value ?? '—'}</div></div>
}
