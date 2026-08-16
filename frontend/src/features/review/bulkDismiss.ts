export interface BulkDismissTarget {
  kind: string;
  label: string;
  count: number;
}

// A failure that repeats per imported row floods the queue with identical
// items (172 tickets produced 172 "malformed llm" questions). Any kind with
// enough duplicates earns a one-click bulk dismissal.
export function bulkDismissTargets(
  items: { kind: string }[], threshold = 3,
): BulkDismissTarget[] {
  const counts = new Map<string, number>();
  for (const item of items) counts.set(item.kind, (counts.get(item.kind) || 0) + 1);
  return [...counts.entries()]
    .filter(([, count]) => count >= threshold)
    .map(([kind, count]) => ({ kind, label: kind.replaceAll("_", " "), count }))
    .sort((left, right) => right.count - left.count);
}
