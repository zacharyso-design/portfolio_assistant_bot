from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Protocol


class ModelPreferenceError(RuntimeError):
    """A safe, non-secret-bearing model preference storage failure."""


MAX_MODEL_ID_LENGTH = 256


def valid_model_id(value: str) -> bool:
    return (
        0 < len(value) <= MAX_MODEL_ID_LENGTH
        and all(ord(character) >= 32 and ord(character) != 127 for character in value)
    )


class ModelPreferenceStore(Protocol):
    def load(self) -> dict[str, str] | None: ...
    def save(self, routine_model: str, judgment_model: str) -> None: ...


class JsonModelPreferenceStore:
    """Stores the current user's non-secret model choices outside OneDrive."""

    def __init__(self, path: Path | None = None):
        local_app_data = os.environ.get("LOCALAPPDATA")
        default_root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        self.path = path or default_root / "PortfolioAssistant" / "settings" / "llm-models.json"

    def load(self) -> dict[str, str] | None:
        if not self.path.exists():
            return None
        try:
            decoded = json.loads(self.path.read_text(encoding="utf-8"))
            routine = decoded["routine_model"].strip()
            judgment = decoded["judgment_model"].strip()
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ModelPreferenceError("Saved model preferences could not be read") from exc
        if not valid_model_id(routine) or not valid_model_id(judgment):
            raise ModelPreferenceError("Saved model preferences could not be read")
        return {"routine_model": routine, "judgment_model": judgment}

    def save(self, routine_model: str, judgment_model: str) -> None:
        payload = {
            "routine_model": routine_model.strip(),
            "judgment_model": judgment_model.strip(),
        }
        if not valid_model_id(payload["routine_model"]) or not valid_model_id(payload["judgment_model"]):
            raise ModelPreferenceError("Choose valid routine and judgment models")
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ModelPreferenceError("Model preferences could not be saved") from exc
