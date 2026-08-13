# CHIO Portfolio Assistant

CHIO Portfolio Assistant is a single-user Windows application for preserving project material, maintaining cited project knowledge, importing cumulative ServiceNow exports, and asking one project questions against its local evidence. It binds only to `127.0.0.1`, stores SQLite outside OneDrive, and makes no public runtime calls.

## Install from source

Prerequisites: Windows, Python 3.11 or later, and Node.js only for the one-time frontend build.

```powershell
Copy-Item config.example.toml config.toml
# Edit config.toml before continuing.
.\scripts\Install.ps1
```

Set `one_drive_root` to the locally synced portfolio root. Configure only the approved internal LLM endpoint. Put its key in the environment variable named by `llm.api_key_env`; never put the key in TOML.

Start in the background and open `http://127.0.0.1:8765`:

```powershell
.\scripts\Run.ps1
```

For visible diagnostics use `.\scripts\Run.ps1 -Foreground`. Stop the background process with `.\scripts\Stop.ps1`.

## Windows distribution (no Node.js required)

Build on an approved build workstation:

```powershell
.\scripts\Build-Distribution.ps1
```

The output is `dist\CHIO-Portfolio-Assistant-Windows.zip`. Extract it, copy `config.example.toml` to `config.toml`, configure it, then run:

```powershell
.\PortfolioAssistant.exe --config .\config.toml migrate
.\PortfolioAssistant.exe --config .\config.toml serve
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
.\.venv\Scripts\python.exe -m portfolio_assistant --config config.toml acceptance-setup --projects 250
```

`config-test` never displays secrets. The fake adapter is for fictional tests and demonstrations only; production configuration uses `adapter = "internal"`.

## Test

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
Push-Location frontend; npm ci; npm run build; Pop-Location
```

## Backup and recovery

Stop the application, then copy the configured `database_path` file and the complete configured `one_drive_root`. A consistent backup requires both. SQLite WAL/SHM files can exist while the app is running, which is why stopping first matters. Originals, source records, updates, and review history are append-oriented; the application has no broad cleanup command.

If processing is interrupted, startup safely recovers `processing` records and retries captured/pending work within configured limits. Use **Retry pending** after endpoint recovery.

## Production values still required

- Actual locally synced government OneDrive root.
- Internal LLM base URL, chat path, authentication header/scheme, key environment variable, and model.
- DoD CA bundle path when the approved endpoint certificate chain requires one.
- Approved GFE package/install method.
- Confirmation that Task Scheduler is permitted and the desired local run time.

See [implementation evidence](IMPLEMENTATION_STATUS.md), [architecture](docs/ARCHITECTURE.md), [data model](docs/DATA_MODEL.md), [LLM adapter](docs/LLM_ADAPTER.md), and [security](docs/SECURITY.md).

The user-supplied **Codex Build Handoff — CHIO Portfolio Assistant, Revision 1 (2026-08-12)** controls scope. The two repository requirement files are retained only as explicitly superseded background references.
