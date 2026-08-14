import type { ReactNode } from "react";

export function Metric({ label, value, tone = "" }: { label: string; value: ReactNode; tone?: string }) {
  return <div className={`metric ${tone}`}><strong>{value}</strong><span>{label}</span></div>;
}
