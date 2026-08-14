import { backend } from "../api/backend";
import type { ProjectDetail } from "../api/contracts";

const pause = (milliseconds: number) => new Promise(resolve => setTimeout(resolve, milliseconds));

export async function pollProjectSource(projectId: string, sourceId: number, refresh: () => Promise<ProjectDetail | void>) {
  for (let attempt = 0; attempt < 90; attempt += 1) {
    const refreshed = await refresh();
    const detail = refreshed || await backend.project.get(projectId);
    const state = detail.sources.find(source => source.id === sourceId)?.processing_state;
    if (!state || !["captured", "processing"].includes(state)) return;
    await pause(1000);
  }
}

export async function pollRefresh(refresh: () => Promise<ProjectDetail | void> | Promise<void>, attempts = 15) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const detail = await refresh();
    // Project refreshes can stop early; Review Queue has no source state and intentionally polls the full window.
    if (detail && !detail.sources.some(source => ["captured", "processing"].includes(source.processing_state))) return;
    if (attempt + 1 < attempts) await pause(1000);
  }
}
