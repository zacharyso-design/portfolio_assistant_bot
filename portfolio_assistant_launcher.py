from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path


RELAUNCHED_ENV = "PORTFOLIO_ASSISTANT_RELAUNCHED"
ONE_DRIVE_ROOT_LINE = re.compile(r"^[ \t]*one_drive_root[ \t]*=[^\r\n]*$", re.MULTILINE)


def _pause() -> None:
    try:
        input("Press Enter to close this window...")
    except (EOFError, KeyboardInterrupt):
        pass


def _application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _source_python(root: Path) -> Path:
    return root / ".venv" / "Scripts" / "python.exe"


def _default_archive_root(root: Path) -> Path:
    value = os.environ.get("OneDriveCommercial")
    if value:
        return Path(value).expanduser().resolve() / "CHIO Portfolio"
    return root / ".runtime" / "one-drive"


def _ensure_config(root: Path) -> Path:
    config_path = root / "config.toml"
    if config_path.is_file():
        return config_path
    example_path = root / "config.example.toml"
    if not example_path.is_file():
        raise FileNotFoundError(f"Configuration template not found: {example_path}")
    archive_root = _default_archive_root(root)
    archive_root.mkdir(parents=True, exist_ok=True)
    escaped_archive_root = str(archive_root).replace("\\", "\\\\").replace('"', '\\"')
    example = example_path.read_text(encoding="utf-8")
    # A callable replacement keeps Windows backslashes out of regex replacement parsing.
    configured, replacements = ONE_DRIVE_ROOT_LINE.subn(
        lambda _match: f'one_drive_root = "{escaped_archive_root}"',
        example,
    )
    if replacements != 1:
        raise ValueError("The configuration template must contain exactly one one_drive_root setting.")
    config_path.write_text(configured, encoding="utf-8")
    print(f"Created configuration: {config_path}")
    print(f"Project archive: {archive_root}")
    return config_path


def _install_source(root: Path) -> int:
    installer = root / "scripts" / "Install.ps1"
    if not installer.is_file():
        print(f"Installer not found: {installer}")
        return 2
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        print("Windows PowerShell is required to install the application.")
        return 2
    print("First-time setup: installing the Portfolio Assistant. This may take a few minutes...")
    try:
        completed = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(installer),
                "-PythonExecutable",
                sys.executable,
            ],
            cwd=root,
            check=False,
        )
    except OSError as exc:
        print(f"Could not run the installer: {exc}")
        return 2
    return completed.returncode


def _is_same_executable(first: Path, second: Path) -> bool:
    try:
        return first.resolve().samefile(second.resolve())
    except OSError:
        return first.resolve() == second.resolve()


def _exit_code(result: int | None) -> int:
    return 0 if result is None else result


def run(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    double_clicked = not arguments
    relaunched = os.environ.get(RELAUNCHED_ENV) == "1"
    pause_here = double_clicked and not relaunched
    root = _application_root()

    config_path: Path | None = None
    if double_clicked:
        # Config must exist before Install.ps1 so its first run reaches database migration.
        try:
            config_path = _ensure_config(root)
        except (OSError, ValueError) as exc:
            print(f"Could not create the configuration: {exc}")
            if pause_here:
                _pause()
            return 2

    if not getattr(sys, "frozen", False):
        project_python = _source_python(root)
        if not project_python.is_file():
            if not double_clicked:
                print(f"The project environment is missing: {project_python}")
                print("Double-click Start CHIO Portfolio Assistant.cmd to run first-time setup.")
                return 2
            install_result = _install_source(root)
            if install_result:
                if pause_here:
                    _pause()
                return install_result
            if not project_python.is_file():
                print(f"Installation completed without creating the project environment: {project_python}")
                if pause_here:
                    _pause()
                return 2
        if not _is_same_executable(Path(sys.executable), project_python):
            if relaunched:
                print(f"Could not switch to the project environment: {project_python}")
                return 2
            child_environment = os.environ.copy()
            child_environment[RELAUNCHED_ENV] = "1"
            try:
                completed = subprocess.run(
                    [str(project_python), str(root / Path(__file__).name), *arguments],
                    cwd=root,
                    check=False,
                    env=child_environment,
                )
            except OSError as exc:
                print(f"Could not start the project environment: {exc}")
                if pause_here:
                    _pause()
                return 2
            if completed.returncode and pause_here:
                _pause()
            return completed.returncode
        # Do not leak the bootstrap marker into processes started by the application.
        os.environ.pop(RELAUNCHED_ENV, None)

    try:
        # Keep this import deferred until the source environment has been validated.
        from portfolio_assistant.cli import main

        if double_clicked:
            if config_path is None:
                print("Could not determine the configuration path.")
                if pause_here:
                    _pause()
                return 2
            result = _exit_code(main(["--config", str(config_path), "launch"]))
            if result and pause_here:
                _pause()
            return result
        return _exit_code(main(arguments))
    except SystemExit as exc:
        if exc.code is None:
            exit_code = 0
        elif isinstance(exc.code, int):
            exit_code = exc.code
        else:
            print(exc.code)
            exit_code = 1
        if pause_here and exit_code:
            _pause()
        return exit_code
    except Exception as exc:
        print(f"CHIO Portfolio Assistant could not start: {exc}")
        traceback.print_exc()
        if pause_here:
            _pause()
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
