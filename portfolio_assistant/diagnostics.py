"""Publish the diagnostic log to a private GitHub repository.

The app runs on a locked-down GFE workstation where the person debugging it
cannot read files directly; before this existed, diagnostics traveled by
pasting log fragments between emails. The publisher pushes the rotating local
log to a PRIVATE companion repository over the GitHub Contents API - pure
httpx, no git binary, no shell - so it works under Constrained Language Mode.

Never enabled implicitly: it runs only when [diagnostics] repo is configured
and the token environment variable is set, and it refuses to upload anything
until GitHub confirms the target repository is private. Failures degrade to a
quiet local log line, never to an app error.
"""
from __future__ import annotations

import base64
import logging
import os
import platform
import threading
from typing import Any, Callable
from urllib.parse import quote

import httpx

from .config import Settings, diagnostic_log_path

LOGGER = logging.getLogger(__name__)

# The API origin is fixed: making it configurable would let a tampered config
# file redirect the token to an arbitrary host.
GITHUB_API = "https://api.github.com"

# Publish at most this much of the log tail per push. Rotation caps the file
# at 2 MB; the tail is what debugging needs, and small pushes stay fast on a
# slow network.
TAIL_BYTES = 512 * 1024
REQUEST_TIMEOUT_SECONDS = 30.0


class DiagnosticLogPublisher:
    def __init__(
        self, settings: Settings, *,
        client_factory: Callable[..., Any] = httpx.Client,
        hostname: str | None = None,
    ):
        self.settings = settings
        self.log_path = diagnostic_log_path(settings.app)
        self._client_factory = client_factory
        self._hostname = hostname or platform.node() or "unknown-host"
        self._published_state: tuple[int, float] | None = None
        self._known_sha: str | None = None
        self._last_error: str | None = None
        # None = unverified, True = confirmed private, False = refused.
        self._repo_private: bool | None = None

    @property
    def repo(self) -> str:
        return self.settings.diagnostics.repo

    def _token(self) -> str:
        return os.environ.get(self.settings.diagnostics.token_env, "").strip()

    def _repo_url(self) -> str:
        return f"{GITHUB_API}/repos/{self.repo}"

    def _contents_url(self) -> str:
        host = quote(self._hostname, safe="")
        return f"{self._repo_url()}/contents/logs/{host}/assistant.log"

    def _current_state(self) -> tuple[int, float] | None:
        try:
            stat = self.log_path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime)

    def should_publish(self) -> bool:
        if not self.repo or not self._token() or self._repo_private is False:
            return False
        state = self._current_state()
        return state is not None and state != self._published_state

    def _tail(self) -> bytes:
        with self.log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size <= TAIL_BYTES:
                handle.seek(0)
                return handle.read()
            handle.seek(size - TAIL_BYTES)
            tail = handle.read()
        # Drop the partial first line so the upload starts on a complete
        # UTF-8 record instead of mid-character or mid-message.
        if b"\n" in tail:
            tail = tail.split(b"\n", 1)[1]
        return b"[... earlier log truncated ...]\n" + tail

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _verify_repo_private(self, client: Any, headers: dict[str, str]) -> bool:
        """Confirm the target is private before the first byte is uploaded.

        An operator typo pointing at a public repository would otherwise
        publish work data to the world. Unverifiable means not publishable.
        """
        if self._repo_private is not None:
            return self._repo_private
        response = client.get(self._repo_url(), headers=headers)
        if response.status_code != 200:
            self._note_error(f"repository check returned HTTP {response.status_code}")
            return False
        try:
            private = bool(response.json().get("private"))
        except ValueError:
            self._note_error("repository check returned an unreadable response")
            return False
        if not private:
            # Permanent refusal, loudly: this is a misconfiguration, and
            # retrying cannot make a public repository a safe destination.
            self._repo_private = False
            LOGGER.warning(
                "diagnostics repo %s is PUBLIC; refusing to publish logs to it", self.repo
            )
            return False
        self._repo_private = True
        return True

    def publish_once(self) -> bool:
        """Push the current log tail; True when a push happened and stuck."""
        if not self.should_publish():
            return False
        state = self._current_state()
        try:
            payload = self._tail()
        except OSError as exc:
            self._note_error(f"log read failed: {exc}")
            return False
        headers = self._headers()
        body = {
            "message": f"log update from {self._hostname}",
            "content": base64.b64encode(payload).decode("ascii"),
        }
        try:
            with self._client_factory(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as client:
                if not self._verify_repo_private(client, headers):
                    return False
                sha = self._remote_sha(client, headers)
                if sha:
                    body["sha"] = sha
                response = client.put(self._contents_url(), headers=headers, json=body)
                if response.status_code == 409:
                    # Another instance updated the file between our GET and
                    # PUT; refresh the sha once and retry.
                    body["sha"] = self._remote_sha(client, headers, force=True) or ""
                    response = client.put(self._contents_url(), headers=headers, json=body)
                if response.status_code not in {200, 201}:
                    self._note_error(f"GitHub returned HTTP {response.status_code}")
                    return False
                try:
                    content = response.json().get("content") or {}
                    self._known_sha = content.get("sha")
                except ValueError:
                    self._known_sha = None
        except httpx.HTTPError as exc:
            self._note_error(f"network failure: {type(exc).__name__}")
            return False
        # Record the state we read BEFORE publishing: the publish itself may
        # append log lines, and those should ride along on the next tick
        # rather than re-triggering an immediate push loop.
        self._published_state = state
        self._last_error = None
        LOGGER.debug("published %s bytes of diagnostics to %s", len(payload), self.repo)
        return True

    def _remote_sha(self, client: Any, headers: dict[str, str], *, force: bool = False) -> str | None:
        if self._known_sha and not force:
            return self._known_sha
        response = client.get(self._contents_url(), headers=headers)
        if response.status_code == 200:
            try:
                self._known_sha = response.json().get("sha")
            except ValueError:
                self._known_sha = None
            return self._known_sha
        return None

    def _note_error(self, message: str) -> None:
        # WARNING once per distinct consecutive error; repeating it every
        # tick would grow the very log being published.
        if message != self._last_error:
            LOGGER.warning("diagnostics publish failed (%s); will keep retrying quietly", message)
            self._last_error = message

    def run(self, stop: threading.Event) -> None:
        interval = self.settings.diagnostics.publish_interval_seconds

        def attempt() -> None:
            try:
                self.publish_once()
            except Exception:  # noqa: BLE001 - a publisher bug must never kill the app
                LOGGER.warning("diagnostics publisher crashed; continuing", exc_info=True)

        # Publish immediately so a short run still leaves a remote trace, and
        # flush once more at shutdown so the newest lines are not lost.
        attempt()
        while not stop.wait(interval):
            attempt()
        attempt()


def start_publisher(settings: Settings, stop: threading.Event) -> threading.Thread | None:
    """Start the background publisher when configured; None otherwise."""
    if settings.app.testing or not settings.diagnostics.repo:
        return None
    publisher = DiagnosticLogPublisher(settings)
    if not publisher._token():
        LOGGER.info(
            "diagnostics repo configured but %s is not set; publishing disabled",
            settings.diagnostics.token_env,
        )
        return None
    thread = threading.Thread(
        target=publisher.run, args=(stop,), name="diagnostics-log-publisher", daemon=True
    )
    thread.start()
    return thread
