export interface BulkDismissTarget {
  kind: string;
  label: string;
  count: number;
}

// Kinds that are pure automatic-failure noise; must mirror the backend's
// BULK_DISMISSABLE_REVIEW_KINDS. Kinds whose resolution carries required
// side effects (project_fit, multi_project_route, action items) are
// individual decisions and never get a bulk button.
export const BULK_DISMISSABLE_KINDS = new Set([
  "malformed_llm", "snow_invalid_row", "snow_unparseable_comments",
]);

// A failure that repeats per imported row floods the queue with identical
// items (172 tickets produced 172 "malformed llm" questions). Any dismissable
// kind with enough duplicates earns a one-click bulk dismissal.
export function bulkDismissTargets(
  items: { kind: string }[], threshold = 3,
): BulkDismissTarget[] {
  const counts = new Map<string, number>();
  for (const item of items) {
    if (BULK_DISMISSABLE_KINDS.has(item.kind)) {
      counts.set(item.kind, (counts.get(item.kind) || 0) + 1);
    }
  }
  return [...counts.entries()]
    .filter(([, count]) => count >= threshold)
    .map(([kind, count]) => ({ kind, label: kind.replaceAll("_", " "), count }))
    .sort((left, right) => right.count - left.count);
}
