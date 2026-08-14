import type { ReactNode } from "react";

export function PageHeader({ eyebrow, title, children }: { eyebrow: string; title: string; children?: ReactNode }) {
  return <header className="topbar"><div><small>{eyebrow}</small><h1>{title}</h1></div><div className="top-actions">{children}</div></header>;
}
