"""
Scheduler configuration loader with hot-reload on file change.

Loads scheduler_config.yaml from a configurable path. Watches mtime
and reloads when the file changes. Falls back to sensible defaults
if the file is missing.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "scheduler_config.yaml"


@dataclass
class SchedulerConfig:
    max_concurrent_heavy: int = 2
    memory_ceiling_pct: float = 75.0
    cpu_ceiling_pct: float = 90.0
    cool_down_after_failure_s: int = 300
    starvation_boost_after_minutes: int = 60
    eta_lookahead: bool = True
    stage_weights: dict = field(default_factory=dict)


_cached_config: SchedulerConfig | None = None
_cached_mtime: float = 0.0
_config_path: Path = _DEFAULT_CONFIG_PATH


def set_config_path(path: str | Path) -> None:
    global _config_path, _cached_config, _cached_mtime
    _config_path = Path(path)
    _cached_config = None
    _cached_mtime = 0.0


def load_config(force_reload: bool = False) -> SchedulerConfig:
    """Load scheduler config, hot-reloading if the file has changed."""
    global _cached_config, _cached_mtime

    if not _config_path.exists():
        if _cached_config is None:
            _cached_config = SchedulerConfig()
        return _cached_config

    try:
        mtime = os.path.getmtime(_config_path)
    except OSError:
        if _cached_config is None:
            _cached_config = SchedulerConfig()
        return _cached_config

    if _cached_config is not None and mtime == _cached_mtime and not force_reload:
        return _cached_config

    try:
        with open(_config_path) as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Failed to load scheduler config from %s: %s", _config_path, exc)
        if _cached_config is None:
            _cached_config = SchedulerConfig()
        return _cached_config

    host_rules = raw.get("host_rules", {})
    scheduling = raw.get("scheduling", {})

    _cached_config = SchedulerConfig(
        max_concurrent_heavy=host_rules.get("max_concurrent_heavy", 2),
        memory_ceiling_pct=host_rules.get("memory_ceiling_pct", 75.0),
        cpu_ceiling_pct=host_rules.get("cpu_ceiling_pct", 90.0),
        cool_down_after_failure_s=host_rules.get("cool_down_after_failure_s", 300),
        starvation_boost_after_minutes=scheduling.get("starvation_boost_after_minutes", 60),
        eta_lookahead=scheduling.get("eta_lookahead", True),
        stage_weights=raw.get("stage_weights", {}),
    )
    _cached_mtime = mtime
    logger.info("Loaded scheduler config from %s", _config_path)
    return _cached_config
