export const apiLinks = {
  sourceOriginal: (sourceId: number) => `/api/sources/${sourceId}/original`,
  citationOriginal: (citationId: string) => `/api/citations/${citationId}/original`,
  originalFile: (originalFileId: number) => `/api/original-files/${originalFileId}`,
};
