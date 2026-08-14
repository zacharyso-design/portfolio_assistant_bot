# Frontend architecture

The browser interface is intentionally replaceable. It consumes the Python application only through the stable HTTP boundary in `src/api`.

## Module boundaries

- `src/api/contracts.ts` describes backend response shapes without importing React.
- `src/api/client.ts` owns HTTP mechanics and error normalization.
- `src/api/backend.ts` owns request endpoints and `src/api/links.ts` owns download URLs.
- `src/app` owns composition, navigation, and the application shell.
- `src/features/<feature>` owns each page and feature-specific presentation.
- `src/components` contains reusable visual primitives with no endpoint knowledge.
- `src/lib` contains UI-independent browser utilities.
- `src/styles/tokens.css` is the branding control surface; the remaining styles are split by responsibility.

To replace the UI later, retain `src/api` (or reimplement its documented contract) and replace `src/app`, `src/features`, `src/components`, and `src/styles`. No Python service, database migration, or archive change should be required for a visual redesign.

## Change rules

1. Add backend requests only in `src/api/backend.ts` and download URLs only in `src/api/links.ts`; feature components should never embed `/api/...` URLs.
2. Keep reusable components free of feature-specific data fetching.
3. Add new pages under `src/features`, then compose them in `src/app/App.tsx`.
4. Prefer theme tokens over repeating brand values in feature CSS.
5. Run `npm run build` before committing so the Node-free `frontend/dist` bundle stays current.
