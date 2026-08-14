import type { Citation } from "../api/contracts";

export function Citations({ items, onOpen }: { items: Citation[]; onOpen: (item: Citation) => void }) {
  return <div className="citations">{items.map((item, index) => <button key={`${item.source_id}-${item.chunk_id}`} onClick={() => onOpen(item)}>[{index + 1}] {item.original_filename || item.display_name || `Source ${item.source_id}`}{item.locator ? ` · ${item.locator}` : ""}</button>)}</div>;
}
