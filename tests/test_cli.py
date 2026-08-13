from __future__ import annotations

import json
import threading

from portfolio_assistant import cli


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
