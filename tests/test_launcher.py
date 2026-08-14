from __future__ import annotations

from pathlib import Path

import portfolio_assistant_launcher as launcher


def test_launcher_reports_missing_source_environment(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_application_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_pause", lambda: None)
    assert launcher.run([]) == 2
    assert "Run scripts\\Install.ps1 once" in capsys.readouterr().out


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
