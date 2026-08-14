from __future__ import annotations

import tomllib
from pathlib import Path

import portfolio_assistant_launcher as launcher


def test_double_click_installs_missing_source_environment(tmp_path: Path, monkeypatch):
    (tmp_path / "config.toml").touch()
    installed: list[Path] = []

    def fake_install(root: Path) -> int:
        installed.append(root)
        project_python = root / ".venv" / "Scripts" / "python.exe"
        project_python.parent.mkdir(parents=True)
        project_python.touch()
        return 0

    class Completed:
        returncode = 0

    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_install_source", fake_install)
    monkeypatch.setattr(launcher, "_is_same_executable", lambda *_: False)
    monkeypatch.setattr(launcher.subprocess, "run", lambda *_args, **_options: Completed())
    assert launcher.run([]) == 0
    assert installed == [tmp_path]


def test_double_click_creates_config_for_organizational_onedrive(tmp_path: Path, monkeypatch):
    one_drive = tmp_path / "Organization OneDrive"
    template = Path(launcher.__file__).with_name("config.example.toml").read_text(encoding="utf-8")
    (tmp_path / "config.example.toml").write_text(template, encoding="utf-8")
    monkeypatch.setenv("OneDriveCommercial", str(one_drive))
    config_path = launcher._ensure_config(tmp_path)
    expected_archive = one_drive / "CHIO Portfolio"
    assert expected_archive.is_dir()
    with config_path.open("rb") as handle:
        assert Path(tomllib.load(handle)["app"]["one_drive_root"]) == expected_archive


def test_double_click_config_falls_back_when_onedrive_is_unavailable(tmp_path: Path, monkeypatch):
    template = Path(launcher.__file__).with_name("config.example.toml").read_text(encoding="utf-8")
    (tmp_path / "config.example.toml").write_text(template, encoding="utf-8")
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.setenv("OneDrive", str(tmp_path / "Personal OneDrive"))
    config_path = launcher._ensure_config(tmp_path)
    expected_archive = tmp_path / ".runtime" / "one-drive"
    assert expected_archive.is_dir()
    with config_path.open("rb") as handle:
        assert Path(tomllib.load(handle)["app"]["one_drive_root"]) == expected_archive


def test_install_source_uses_current_python(tmp_path: Path, monkeypatch):
    installer = tmp_path / "scripts" / "Install.ps1"
    installer.parent.mkdir()
    installer.touch()
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0

    def fake_run(command, **options):
        captured.update({"command": command, "options": options})
        return Completed()

    monkeypatch.setattr(launcher.shutil, "which", lambda _name: "powershell.exe")
    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    assert launcher._install_source(tmp_path) == 0
    assert captured["command"][-2:] == ["-PythonExecutable", launcher.sys.executable]
    assert captured["options"]["cwd"] == tmp_path


def test_double_click_pauses_when_first_time_install_fails(tmp_path: Path, monkeypatch):
    (tmp_path / "config.toml").touch()
    paused: list[bool] = []
    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_install_source", lambda _root: 5)
    monkeypatch.setattr(launcher, "_pause", lambda: paused.append(True))
    assert launcher.run([]) == 5
    assert paused == [True]


def test_launcher_reexecutes_with_project_environment(tmp_path: Path, monkeypatch):
    project_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    project_python.parent.mkdir(parents=True)
    project_python.touch()
    captured: dict[str, object] = {}

    class Completed:
        returncode = 7

    def fake_run(command, **options):
        captured.update({"command": command, "options": options})
        return Completed()

    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_is_same_executable", lambda *_: False)
    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    assert launcher.run(["config-test"]) == 7
    assert captured["command"] == [
        str(project_python),
        str(tmp_path / "portfolio_assistant_launcher.py"),
        "config-test",
    ]
    assert captured["options"]["cwd"] == tmp_path
    assert captured["options"]["env"][launcher.RELAUNCHED_ENV] == "1"


def test_double_click_reexecutes_with_project_environment(tmp_path: Path, monkeypatch):
    project_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    project_python.parent.mkdir(parents=True)
    project_python.touch()
    (tmp_path / "config.toml").touch()
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0

    def fake_run(command, **options):
        captured.update({"command": command, "options": options})
        return Completed()

    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_is_same_executable", lambda *_: False)
    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    assert launcher.run([]) == 0
    assert captured["command"] == [
        str(project_python),
        str(tmp_path / "portfolio_assistant_launcher.py"),
    ]


def test_double_click_pauses_when_project_environment_cannot_start(tmp_path: Path, monkeypatch):
    project_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    project_python.parent.mkdir(parents=True)
    project_python.touch()
    (tmp_path / "config.toml").touch()
    paused: list[bool] = []
    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_is_same_executable", lambda *_: False)
    monkeypatch.setattr(launcher, "_pause", lambda: paused.append(True))

    def fail_to_start(*_args, **_options):
        raise OSError("blocked")

    monkeypatch.setattr(launcher.subprocess, "run", fail_to_start)
    assert launcher.run([]) == 2
    assert paused == [True]


def test_double_click_parent_pauses_when_relaunched_child_fails(tmp_path: Path, monkeypatch):
    project_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    project_python.parent.mkdir(parents=True)
    project_python.touch()
    (tmp_path / "config.toml").touch()
    paused: list[bool] = []

    class Completed:
        returncode = 3

    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_is_same_executable", lambda *_: False)
    monkeypatch.setattr(launcher, "_pause", lambda: paused.append(True))
    monkeypatch.setattr(launcher.subprocess, "run", lambda *_args, **_options: Completed())
    assert launcher.run([]) == 3
    assert paused == [True]


def test_relaunch_guard_prevents_recursive_processes(tmp_path: Path, monkeypatch):
    project_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    project_python.parent.mkdir(parents=True)
    project_python.touch()
    (tmp_path / "config.toml").touch()
    paused: list[bool] = []
    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_is_same_executable", lambda *_: False)
    monkeypatch.setattr(launcher, "_pause", lambda: paused.append(True))
    monkeypatch.setenv(launcher.RELAUNCHED_ENV, "1")
    assert launcher.run([]) == 2
    assert paused == []


def test_double_click_pauses_on_system_exit(tmp_path: Path, monkeypatch):
    project_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    project_python.parent.mkdir(parents=True)
    project_python.touch()
    (tmp_path / "config.toml").touch()
    paused: list[bool] = []
    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_is_same_executable", lambda *_: True)
    monkeypatch.setattr(launcher, "_pause", lambda: paused.append(True))
    monkeypatch.setattr("portfolio_assistant.cli.main", lambda _arguments: (_ for _ in ()).throw(SystemExit(2)))
    assert launcher.run([]) == 2
    assert paused == [True]


def test_double_click_normalizes_none_exit_code(tmp_path: Path, monkeypatch):
    project_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    project_python.parent.mkdir(parents=True)
    project_python.touch()
    (tmp_path / "config.toml").touch()
    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_is_same_executable", lambda *_: True)
    monkeypatch.setattr("portfolio_assistant.cli.main", lambda _arguments: None)
    assert launcher.run([]) == 0


def test_frozen_launcher_skips_source_environment(tmp_path: Path, monkeypatch):
    executable = tmp_path / "PortfolioAssistant.exe"
    executable.touch()
    (tmp_path / "config.toml").touch()
    captured: list[str] = []
    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher.sys, "executable", str(executable))
    monkeypatch.setattr("portfolio_assistant.cli.main", lambda arguments: captured.extend(arguments) or 0)
    assert launcher.run([]) == 0
    assert captured == ["--config", str(tmp_path / "config.toml"), "launch"]


def test_double_click_defaults_to_launch_with_root_config(tmp_path: Path, monkeypatch):
    project_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    project_python.parent.mkdir(parents=True)
    project_python.touch()
    (tmp_path / "config.toml").touch()
    captured: list[str] = []
    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_is_same_executable", lambda *_: True)
    monkeypatch.setattr("portfolio_assistant.cli.main", lambda arguments: captured.extend(arguments) or 0)
    assert launcher.run([]) == 0
    assert captured == ["--config", str(tmp_path / "config.toml"), "launch"]
