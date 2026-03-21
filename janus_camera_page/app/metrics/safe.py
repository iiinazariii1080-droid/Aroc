"""Safe metric helpers — single point for try/except ImportError.

Replaces the 15+ inline ``try: from app.metrics import X; X.inc()
except ImportError: pass`` blocks scattered across the codebase.

All functions are no-ops when ``prometheus_client`` is not installed
or when the named metric does not exist.

Errors are logged once per unique (function, metric_name) pair so that
typos and misconfigurations surface in logs instead of being silently
swallowed.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set

_log = logging.getLogger(__name__)

# Cache of (func_name, metric_name) pairs that already logged an error.
# Prevents log spam while ensuring at least one warning per bad call site.
_warned: Set[tuple[str, str]] = set()


def _warn_once(func: str, metric_name: str, exc: Exception) -> None:
    key = (func, metric_name)
    if key not in _warned:
        _warned.add(key)
        _log.warning(
            "metrics.%s(%r) failed (will not warn again): %s", func, metric_name, exc
        )


def safe_inc(
    metric_name: str,
    labels: Optional[Dict[str, Any]] = None,
    value: float = 1,
) -> None:
    """Increment a Prometheus Counter (or Gauge) by *value*.

    No-op if ``app.metrics`` is not importable or if *metric_name* is
    not defined in the registry.
    """
    try:
        from app import metrics as _m

        metric = getattr(_m, metric_name, None)
        if metric is None:
            _warn_once("safe_inc", metric_name, ValueError(f"metric {metric_name!r} not found"))
            return
        if labels:
            metric.labels(**labels).inc(value)
        else:
            metric.inc(value)
    except ImportError:
        pass  # prometheus_client not installed — genuine no-op
    except Exception as exc:
        _warn_once("safe_inc", metric_name, exc)


def safe_set(
    metric_name: str,
    value: float,
    labels: Optional[Dict[str, Any]] = None,
) -> None:
    """Set a Prometheus Gauge to *value*.

    No-op if ``app.metrics`` is not importable or if *metric_name* is
    not defined in the registry.
    """
    try:
        from app import metrics as _m

        metric = getattr(_m, metric_name, None)
        if metric is None:
            _warn_once("safe_set", metric_name, ValueError(f"metric {metric_name!r} not found"))
            return
        if labels:
            metric.labels(**labels).set(value)
        else:
            metric.set(value)
    except ImportError:
        pass  # prometheus_client not installed — genuine no-op
    except Exception as exc:
        _warn_once("safe_set", metric_name, exc)
