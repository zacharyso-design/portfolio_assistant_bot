export function PriorityPill({ value }: { value: string }) {
  return <span className={`pill ${value.toLowerCase()}`}>{value}</span>;
}

export function Status({ value }: { value: string }) {
  return <span className={`status ${value.toLowerCase()}`}><i />{value}</span>;
}
