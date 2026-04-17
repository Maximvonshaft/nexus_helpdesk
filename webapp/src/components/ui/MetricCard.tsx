export function MetricCard({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return <div className="card metric"><div className="metric-value">{value}</div><div className="metric-label">{label}</div>{hint ? <div className="section-subtitle">{hint}</div> : null}</div>
}
