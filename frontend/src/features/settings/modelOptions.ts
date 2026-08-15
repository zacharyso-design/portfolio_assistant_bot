export function mergeModelOptions(
  availableModels: string[],
  routineModel: string,
  judgmentModel: string,
): string[] {
  return Array.from(new Set([
    ...availableModels,
    routineModel,
    judgmentModel,
  ].filter(Boolean))).sort((left, right) => left.localeCompare(right));
}
