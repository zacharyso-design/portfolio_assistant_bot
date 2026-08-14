import type { ReactNode } from "react";

export function Notice({ children, error = false, onDismiss }: { children: ReactNode; error?: boolean; onDismiss?: () => void }) {
  return <div className={`notice ${error ? "error" : ""}`} role={error ? "alert" : "status"}>{children}{onDismiss && <button aria-label="Dismiss" onClick={onDismiss}>×</button>}</div>;
}
