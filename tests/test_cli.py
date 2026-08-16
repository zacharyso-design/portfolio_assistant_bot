from __future__ import annotations

import asyncio
import json
import sys
import threading

from portfolio_assistant import cli


def test_windows_selector_loop_factory_sets_the_current_loop():
    loop = cli._windows_selector_loop()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
        assert asyncio.get_event_loop() is loop
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def test_launch_uses_selector_event_loop_on_windows(settings, monkeypatch):
    observed_loops: list[asyncio.AbstractEventLoop] = []

    class InspectingServer:
        started = True

        def __init__(self, config) -> None:
            self.config = config

        async def serve(self) -> None:
            observed_loops.append(asyncio.get_running_loop())

        def run(self) -> None:
            raise AssertionError("Windows startup must not use Uvicorn's Proactor-based runner")

    def reject_default_runner(*args, **kwargs) -> None:
        raise AssertionError("Windows launch must bypass Uvicorn's Proactor-based runner")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(cli, "load_settings", lambda _: settings)
    monkeypatch.setattr(cli, "_app_is_running", lambda _: False)
    monkeypatch.setattr(cli, "_open_app_when_ready", lambda *args: None)
    monkeypatch.setattr(cli, "create_app", lambda _: object())
    monkeypatch.setattr(cli.uvicorn, "Server", InspectingServer)
    monkeypatch.setattr(cli.uvicorn, "run", reject_default_runner)

    assert cli.main(["--config", "unused.toml", "launch"]) == 0
    assert len(observed_loops) == 1
    assert isinstance(observed_loops[0], asyncio.SelectorEventLoop)


def test_launch_returns_uvicorn_failure_when_server_never_starts(settings, monkeypatch):
    class FailedServer:
        started = False

        def __init__(self, config) -> None:
            self.config = config

        async def serve(self) -> None:
            return None

        def run(self) -> None:
            raise AssertionError("Windows startup must not use Uvicorn's Proactor-based runner")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(cli, "load_settings", lambda _: settings)
    monkeypatch.setattr(cli, "_app_is_running", lambda _: False)
    monkeypatch.setattr(cli, "_open_app_when_ready", lambda *args: None)
    monkeypatch.setattr(cli, "create_app", lambda _: object())
    monkeypatch.setattr(cli.uvicorn, "Server", FailedServer)

    assert cli.main(["--config", "unused.toml", "launch"]) == 3


def test_run_server_preserves_uvicorn_runner_on_other_platforms(monkeypatch):
    calls: list[str] = []

    class InspectingServer:
        started = True

        async def serve(self) -> None:
            raise AssertionError("Non-Windows startup must retain Uvicorn's runner")

        def run(self) -> None:
            calls.append("run")

    monkeypatch.setattr(sys, "platform", "linux")
    assert cli._run_server(InspectingServer()) == 0
    assert calls == ["run"]


def test_run_server_treats_keyboard_interrupt_as_normal_windows_shutdown(monkeypatch, capsys):
    class InterruptingServer:
        started = True

        async def serve(self) -> None:
            raise KeyboardInterrupt

        def run(self) -> None:
            raise AssertionError("Windows startup must not use Uvicorn's Proactor-based runner")

    monkeypatch.setattr(sys, "platform", "win32")
    assert cli._run_server(InterruptingServer()) == 0
    assert capsys.readouterr().err == ""


def test_run_server_treats_keyboard_interrupt_as_normal_non_windows_shutdown(monkeypatch):
    class InterruptingServer:
        started = True

        async def serve(self) -> None:
            raise AssertionError("Non-Windows startup must retain Uvicorn's runner")

        def run(self) -> None:
            raise KeyboardInterrupt

    monkeypatch.setattr(sys, "platform", "linux")
    assert cli._run_server(InterruptingServer()) == 0


def test_serve_reload_passes_an_import_string(settings, monkeypatch):
    # uvicorn's reloader re-imports the app in a child process and rejects an
    # app instance with exit code 1, so --reload must get an import string.
    # The previous version of this test asserted the instance was passed,
    # which kept the suite green while the command always failed.
    import importlib

    calls: list[tuple[object, dict[str, object]]] = []
    validations: list[object] = []

    def record_run(selected_app, **kwargs) -> None:
        calls.append((selected_app, kwargs))

    monkeypatch.setattr(cli, "load_settings", lambda _: settings)
    monkeypatch.setattr(cli, "create_app", lambda s: validations.append(s) or object())
    monkeypatch.setattr(cli.uvicorn, "run", record_run)

    assert cli.main(["--config", "unused.toml", "serve", "--reload"]) == 0
    # The parent validates the configuration by building the app once before
    # the reloader starts, so config errors surface as Error: lines, not as
    # endlessly retried child tracebacks.
    assert validations == [settings]
    assert calls == [("portfolio_assistant.api:create_app", {
        "factory": True,
        "host": "127.0.0.1",
        "port": 8765,
        "reload": True,
        "log_config": None,
    })]
    module_name, _, attribute = calls[0][0].partition(":")
    assert callable(getattr(importlib.import_module(module_name), attribute))


def test_serve_without_reload_keeps_the_app_instance(settings, monkeypatch):
    app = object()
    calls: list[tuple[object, dict[str, object]]] = []

    monkeypatch.setattr(cli, "load_settings", lambda _: settings)
    monkeypatch.setattr(cli, "create_app", lambda _: app)
    monkeypatch.setattr(cli.uvicorn, "run", lambda a, **kw: calls.append((a, kw)))

    assert cli.main(["--config", "unused.toml", "serve"]) == 0
    assert calls == [(app, {
        "host": "127.0.0.1",
        "port": 8765,
        "reload": False,
        "log_config": None,
    })]


def test_launch_opens_an_existing_server(settings, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(cli, "load_settings", lambda _: settings)
    monkeypatch.setattr(cli, "_app_is_running", lambda _: True)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url, new: opened.append(url) or True)

    assert cli.main(["--config", "unused.toml", "launch"]) == 0
    assert opened == ["http://127.0.0.1:8765"]


def test_launch_is_available_as_a_cli_command():
    args = cli.build_parser().parse_args(["launch"])
    assert args.command == "launch"


def test_health_probe_discriminates_the_application(settings, monkeypatch):
    class Response:
        def __init__(self, status: int, payload: dict[str, object]):
            self.status = status
            self.payload = payload

        def read(self, _: int) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    class Connection:
        def __init__(self, response: Response | None = None, *, refused: bool = False):
            self.response = response
            self.refused = refused

        def request(self, method: str, path: str) -> None:
            if self.refused:
                raise ConnectionRefusedError

        def getresponse(self) -> Response:
            assert self.response is not None
            return self.response

        def close(self) -> None:
            pass

    cases = (
        (Connection(Response(200, {"ok": True, "application": cli.APPLICATION_ID})), True),
        (Connection(Response(200, {"ok": True, "application": "another-app"})), False),
        (Connection(Response(503, {"ok": False})), False),
        (Connection(refused=True), False),
    )
    for connection, expected in cases:
        monkeypatch.setattr(cli.http.client, "HTTPConnection", lambda *args, **kwargs: connection)
        assert cli._app_is_running(settings) is expected


def test_browser_opener_stops_without_opening(settings, monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(cli, "_app_is_running", lambda _: False)
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_app", lambda _: opened.append("opened") or True)

    cli._open_app_when_ready(settings, stop)

    assert opened == []
