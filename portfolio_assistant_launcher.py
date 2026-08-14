from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path


RELAUNCHED_ENV = "PORTFOLIO_ASSISTANT_RELAUNCHED"


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

    if not getattr(sys, "frozen", False):
        project_python = _source_python(root)
        if not project_python.is_file():
            print(f"The project environment is missing: {project_python}")
            print("Run scripts\\Install.ps1 once, then double-click this launcher again.")
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
            config_path = root / "config.toml"
            if not config_path.is_file():
                print(f"Configuration file not found: {config_path}")
                print("Copy config.example.toml to config.toml and set the government OneDrive path.")
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
