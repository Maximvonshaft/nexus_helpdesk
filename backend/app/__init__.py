from __future__ import annotations

import os
import re
import socket


def _prometheus_process_namespace() -> str:
    raw = (os.getenv("NEXUS_METRICS_PROCESS_NAMESPACE") or socket.gethostname() or "nexus").strip()
    normalized = re.sub(r"[^A-Za-z0-9.-]+", "-", raw).strip("-.")
    return (normalized or "nexus")[:80]


def prometheus_process_identifier(pid: int | str | None = None) -> str:
    """Return an identity unique across Docker PID namespaces and local forks."""
    namespace = _prometheus_process_namespace()
    raw_pid = str(os.getpid() if pid is None else pid).strip()
    if raw_pid.startswith(f"{namespace}-"):
        return raw_pid
    return f"{namespace}-{raw_pid}"


def _configure_prometheus_multiprocess_identity() -> None:
    # prometheus-client normally keys mmap files only by PID. Separate Docker
    # containers commonly all run their main process as PID 1, so a shared
    # registry requires a container namespace before any metric is constructed.
    if not (os.getenv("PROMETHEUS_MULTIPROC_DIR") or "").strip():
        return
    try:
        from prometheus_client import multiprocess, values
    except Exception:  # pragma: no cover - observability remains optional
        return

    values.ValueClass = values.MultiProcessValue(process_identifier=prometheus_process_identifier)

    current_mark = multiprocess.mark_process_dead
    if getattr(current_mark, "_nexus_namespaced", False):
        return

    def mark_process_dead(pid, path=None):  # noqa: ANN001
        return current_mark(prometheus_process_identifier(pid), path=path)

    mark_process_dead._nexus_namespaced = True  # type: ignore[attr-defined]
    multiprocess.mark_process_dead = mark_process_dead


_configure_prometheus_multiprocess_identity()
