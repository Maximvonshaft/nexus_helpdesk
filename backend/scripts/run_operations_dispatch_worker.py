#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.operations_dispatch_runtime import (  # noqa: E402
    AdapterRegistry,
    OperationsDispatchRuntimeConfig,
    run_operations_dispatch_cycle,
)
from app.services.observability import configure_logging  # noqa: E402
from app.settings import get_settings  # noqa: E402

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the isolated, fail-closed Operations Dispatch consumer"
    )
    parser.add_argument("--worker-id", default="worker-operations-dispatch")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--idle-sleep-seconds", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_json)

    # This first production topology is deliberately inert. A later governed
    # delivery may inject a normalized Settings-backed adapter configuration
    # only after first-class Tenant authority and Provider receipt persistence
    # exist. Do not read a side-channel environment flag here.
    config = OperationsDispatchRuntimeConfig(
        mode="disabled",
        adapter_name="disabled",
        app_env=str(settings.app_env or "development"),
        tenant_authority_ready=False,
        idle_sleep_seconds=args.idle_sleep_seconds,
    ).validated()
    registry = AdapterRegistry()

    while True:
        db = SessionLocal()
        try:
            result = run_operations_dispatch_cycle(
                db,
                config=config,
                registry=registry,
                worker_id=args.worker_id,
            )
            LOGGER.info(
                "operations_dispatch_worker_cycle",
                extra={"event_payload": result.as_dict()},
            )
        except Exception:
            LOGGER.exception(
                "operations_dispatch_worker_cycle_failed",
                extra={"event_payload": {"worker_id": str(args.worker_id)[:120]}},
            )
            if args.once:
                return 2
        finally:
            db.close()

        if args.once:
            print(
                "status={status} processed={processed} reason={reason}".format(
                    status=result.status,
                    processed=result.processed,
                    reason=result.reason,
                )
            )
            return 0 if result.status in {"ready", "blocked"} else 2
        time.sleep(config.idle_sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
