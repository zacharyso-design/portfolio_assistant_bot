# CHIO Portfolio Assistant

CHIO Portfolio Assistant is a single-user Windows application for preserving project material, maintaining cited project knowledge, importing cumulative ServiceNow exports, and asking one project questions against its local evidence. It binds only to `127.0.0.1`, stores its rebuildable SQLite index outside OneDrive, and sends AI requests only to the configured government GenAI.mil endpoint.

## Install from source

Prerequisite: Windows with Python 3.11 or later.

After cloning or pulling the repository, double-click **Start CHIO Portfolio Assistant.cmd** in the repository root. On first use it automatically creates `config.toml`, selects the current user's organizational OneDrive (falling back to a local runtime folder), creates `.venv`, installs the Python dependencies, and opens the application. The compiled web interface is included, so Node.js is not required.

For manual installation from PowerShell, copy `config.example.toml` to `config.toml`, set the desired archive path, and run `.\scripts\Install.ps1`. If the config is absent, the script creates it and exits with code 2; edit the new file and run the script again.

If first-time setup is interrupted, double-click the launcher again; it detects and repairs the incomplete installation. The completion marker records that installation finished, not that dependency versions are current. If a future dependency update leaves the environment inconsistent, delete `.venv` and double-click the launcher to rebuild it automatically.

The compiled `frontend\dist` files are deliberately committed for Node-free government-workstation installs. After changing `frontend\src`, run `npm run build` in `frontend`, confirm Vite removed obsolete content-hashed files, and stage both additions and deletions in the refreshed `frontend\dist` output.

The one-click launcher sets `one_drive_root` automatically; edit it in `config.toml` only if you want a different locally synced portfolio root. The supplied LLM defaults target the OpenAI-compatible GenAI.mil chat-completions endpoint. After the bot starts, open **Settings**, paste the GenAI.mil API key, choose **Save encrypted key**, and run **Test API health**. The saved key is encrypted to the current Windows user with DPAPI outside OneDrive and is never written to TOML. An administrator-provided `GENAI_API_KEY` environment variable remains supported and takes priority.

The application creates this durable archive below that root:

```text
CHIO Portfolio Assistant\
  Projects\
  Shared Intake\
  Archive\
```

Each project has one stable-ID folder. Every upload becomes a self-contained ingestion package containing byte-preserved originals, hashes, a manifest, extracted text, citations, and knowledge items. Direct-project uploads remain pending until the LLM project-fit check passes; conflicting or uncertain material stops in Review Queue before any project memory changes. Multi-project material is preserved once in `Shared Intake` and linked into projects only after Review Queue confirmation. Where policy permits, mark the `CHIO Portfolio Assistant` folder **Always keep on this device** so originals are locally available for hashing and citation access.

Keep project folders as direct children of `Projects`; the bot owns that layout, and manual regrouping or moving of managed package folders is not supported. A project's human-readable folder name is fixed when the project is created; renaming the project in the app updates its metadata but does not rename the durable folder.

Start in the background and open `http://127.0.0.1:8765`:

```powershell
.\scripts\Run.ps1
```

For subsequent one-click foreground startup, use the same `.cmd` file or double-click `portfolio_assistant_launcher.py`. The launcher keeps its window open while the bot is running and pauses on startup errors so the message remains visible. For visible PowerShell diagnostics use `.\scripts\Run.ps1 -Foreground`. Stop the background process with `.\scripts\Stop.ps1`.

## Windows distribution (no Node.js required)

Build on an approved build workstation:

```powershell
.\scripts\Build-Distribution.ps1
```

The output is `dist\CHIO-Portfolio-Assistant-Windows.zip`. Extract it, then double-click **Start CHIO Portfolio Assistant.cmd**. On first use it creates `config.toml`, selects an archive location, starts the local server, and opens the application in the default browser; keep its command window open while using the application. Open **Settings** to save the API key and verify GenAI.mil health.

The equivalent manual command is:

```powershell
.\PortfolioAssistant.exe --config .\config.toml launch
```

## Morning update

Install or remove the 0600-by-default local task:

```powershell
.\scripts\Install-MorningTask.ps1
.\scripts\Remove-MorningTask.ps1
```

The installer reads `app.daily_run_time` from TOML. If GFE policy blocks task creation, the portfolio banner displays **Morning task not installed** and **Run update now** remains available.

## CLI operations

```powershell
.\.venv\Scripts\python.exe -m portfolio_assistant --config config.toml migrate
.\.venv\Scripts\python.exe -m portfolio_assistant --config config.toml config-test --connect
.\.venv\Scripts\python.exe -m portfolio_assistant --config config.toml daily
.\.venv\Scripts\python.exe -m portfolio_assistant --config config.toml retry-pending
.\.venv\Scripts\python.exe -m portfolio_assistant --config config.toml rebuild-index
.\.venv\Scripts\python.exe -m portfolio_assistant --config config.toml rescan-onedrive
.\.venv\Scripts\python.exe -m portfolio_assistant --config config.toml acceptance-setup --projects 250
```

`config-test` never displays secrets. The fake adapter is for fictional tests and demonstrations only; production configuration uses `adapter = "internal"`. The health button makes a live key-authenticated request but sends no project data.

`rebuild-index` and `rescan-onedrive` rebuild the local SQLite index from the OneDrive manifests and assistant sidecars without modifying or reprocessing original files. The Portfolio page also provides a **Rescan OneDrive** button.

## Project archive workflow

Open a project and use the upload area to select one file, multiple files, or a folder. Use **Paste a note or transcript** when no original container exists; the archive records the capture method as `pasted_text` and never represents pasted email text as an original `.msg` or `.eml`.

`app.max_file_mb` limits the combined bytes in one ingestion selection, not each individual file.

Project pages separate three review surfaces:

- **Living Summary** shows the current cited project state, independent approval, failure/retry status, and prior-version comparison. Each manual regeneration intentionally creates a new auditable version, even when the eligible knowledge has not changed.
- **Knowledge History** shows chronological cited updates with date, source, category, and review filters.
- **Sources** exposes each package, original files and hashes, the manifest, lifecycle history, errors, and a hash-verified **Rebuild derived files** action that never replaces originals. **Remove from project** moves the whole package into the managed `Archive` and excludes it from the current summary, knowledge, chat, search, updates, and source-created actions; **Restore to project** reverses that operation. **Update project knowledge** regenerates the current summary and durable knowledge sidecar from active sources only.

For a project-fit review, choose **Keep in this project**, select a different project and choose **Move and process**, or choose **Archive without using**. The source is not committed to project memory while that review is open.

## Test

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
Push-Location frontend; npm ci; npm run build; Pop-Location
```

## Backup and recovery

The OneDrive package archive is the durable record. The active SQLite database remains outside OneDrive to avoid sync conflicts and can be rebuilt from the archive. For a complete point-in-time backup of UI/cache state as well, stop the application, then copy the configured `database_path` and `one_drive_root`; SQLite WAL/SHM files can exist while the app is running. Originals, source records, updates, review history, and source lifecycle events are append-oriented, and the application has no broad cleanup command. A source removed through the UI is recoverably archived rather than deleted from disk.

If processing is interrupted, startup safely recovers `processing` records and retries captured/pending work within configured limits. Use **Retry pending** after endpoint recovery.

## Production values still required

- Actual locally synced government OneDrive root.
- An authorized GenAI.mil account and API key, saved in Settings or provided through `GENAI_API_KEY`.
- DoD CA bundle path when the approved endpoint certificate chain requires one.
- Approved GFE package/install method.
- Confirmation that Task Scheduler is permitted and the desired local run time.

See [implementation evidence](IMPLEMENTATION_STATUS.md), [architecture](docs/ARCHITECTURE.md), [data model](docs/DATA_MODEL.md), [LLM adapter](docs/LLM_ADAPTER.md), and [security](docs/SECURITY.md).

The user-supplied **Codex Build Handoff — CHIO Portfolio Assistant, Revision 1 (2026-08-12)** controls scope. The two repository requirement files are retained only as explicitly superseded background references.
