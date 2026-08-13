# Environment spike

Recorded 2026-08-12 on the build workstation before feature implementation.

| Check | Result |
| --- | --- |
| Windows | Windows 11 build `10.0.26200.9168`; Windows PowerShell `5.1.26100.9168` |
| Python | CPython `3.14.4` (64-bit); application supports Python 3.11+ |
| Dependency installation | A clean virtual environment installed every pinned runtime and development dependency successfully from the workstation's configured package source. GFE organizational package approval remains a production input. |
| Node/Vite build | Node `24.15.0`, npm `11.12.1`, Vite `8.2.1`; production uses compiled assets and does not need Node. |
| SQLite | Python SQLite `3.50.4`; FTS5 creation/query succeeded. The bounded `LIKE` fallback remains available behind the same retrieval interface. |
| OneDrive access | Read/write/atomic-rename behavior succeeded against the local development fixture beneath the synced OneDrive workspace. The actual government portfolio root has not yet been supplied. |
| Task Scheduler | A non-elevated one-time test task was created, queried, and deleted successfully. GFE policy may differ; the UI retains the manual fallback. |
| `.msg` parsing | `extract-msg 0.54.1`, no Outlook COM. A fictional MSG created by `msgforge 1.0.0` round-tripped through the production parser with a preserved/extracted attachment. Organizational package approval is still required on the GFE. |
| `.xlsx` | `openpyxl 3.1.5`, read-only/data-only mode. |
| `.docx` | `python-docx 1.2.0`. |
| text-layer PDF | `pypdf 6.0.0`; blank/scanned PDF correctly reports `unsupported`. |
| Web server | `FastAPI 0.116.1`, `Uvicorn 0.35.0`; loopback Host/Origin rejection tests passed. |
| HTTP client | `httpx 0.28.1`; redirects are disabled and endpoint host/scheme are validated. |
| Internal LLM | Endpoint details, model, key method, and DoD CA bundle were not supplied. The internal connection test is implemented and does not block fake-adapter acceptance testing. TLS verification cannot be disabled. |

Decision: use one FastAPI backend, Python SQLite/FTS5, compiled React static assets, `extract-msg`, `openpyxl`, `python-docx`, and `pypdf`. Support both venv installation and a PyInstaller one-folder distribution from the same codebase.
