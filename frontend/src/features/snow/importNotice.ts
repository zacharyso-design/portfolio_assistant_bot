import type { SnowImportResult } from "../../api/contracts";

// A headers-only export (a few KB) reads as zero tickets and previously
// rendered a silent all-zero result panel - indistinguishable from the
// import not running at all.
export function emptyImportNotice(result: SnowImportResult | null): string {
  if (!result || result.tickets_read > 0) return "";
  return "The file was read successfully but contained no ticket rows. "
    + "ServiceNow exports only what the list view shows, so an empty or "
    + "filtered-out list produces a headers-only file (usually just a few KB). "
    + "Open the list in ServiceNow, confirm rows are visible, and export again.";
}
