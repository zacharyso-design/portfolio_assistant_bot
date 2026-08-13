from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

import uvicorn

from .api import create_app
from .config import ConfigurationError, ensure_runtime_paths, load_settings
from .db import Database
from .llm import LlmUnavailable, build_adapter
from .services import PortfolioService, ValidationError


def _service(config: str, *, testing: bool = False) -> PortfolioService:
    settings = load_settings(config, testing=testing)
    ensure_runtime_paths(settings)
    db = Database(settings.app.database_path)
    db.migrate()
    return PortfolioService(settings, db, build_adapter(settings.llm))


def _seed(service: PortfolioService, count: int) -> dict[str, int]:
    groups = service.list_groups()
    for name, order in (("Enterprise Configuration", 10), ("Clinical Operations", 20), ("Governance & Strategy", 30)):
        if not any(group["name"] == name for group in groups):
            service.create_group(name, order)
    groups = [group for group in service.list_groups() if not group["is_system"]]
    existing = service.list_projects(limit=250)["total"]
    created = 0
    for index in range(existing, max(existing, count)):
        group = groups[index % len(groups)]
        project = service.create_project(
            f"Fictional Portfolio Project {index + 1:03d}", portfolio_group_id=group["id"]
        )
        service.update_project(project["id"], {
            "priority": ("Critical", "High", "Medium", "Low")[index % 4],
            "status": ("Green", "Yellow", "Red", "Green")[index % 4],
            "owner_text": f"Fictional Owner {index % 17 + 1}",
            "next_action": f"Validate fictional milestone {index + 1}",
            "next_action_due": date.today().isoformat(),
        })
        created += 1
    return {"created": created, "total": service.list_projects(limit=250)["total"]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portfolio-assistant")
    parser.add_argument("--config", default=os.environ.get("PORTFOLIO_ASSISTANT_CONFIG", "config.toml"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--reload", action="store_true")
    daily = subparsers.add_parser("daily")
    daily.add_argument("--date", type=date.fromisoformat)
    config_test = subparsers.add_parser("config-test")
    config_test.add_argument("--connect", action="store_true")
    setup = subparsers.add_parser("acceptance-setup")
    setup.add_argument("--projects", type=int, default=250)
    subparsers.add_parser("retry-pending")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "serve":
            settings = load_settings(args.config)
            os.environ["PORTFOLIO_ASSISTANT_CONFIG"] = str(Path(args.config).resolve())
            uvicorn.run(
                create_app(settings), host=settings.app.bind_host, port=settings.app.bind_port,
                reload=args.reload, log_config=None,
            )
            return 0
        service = _service(args.config)
        if args.command == "migrate":
            result = {"ok": True, "database": str(service.settings.app.database_path), "retrieval_mode": service.db.fts_mode}
        elif args.command == "daily":
            result = service.run_daily(args.date)
        elif args.command == "config-test":
            result = service.configuration_status(test_llm=args.connect)
        elif args.command == "acceptance-setup":
            result = _seed(service, max(1, min(args.projects, 250)))
        elif args.command == "retry-pending":
            result = service.process_pending(manual=True, limit=100)
        else:
            return 2
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ConfigurationError, ValidationError, LlmUnavailable) as exc:
        print(f"Error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
