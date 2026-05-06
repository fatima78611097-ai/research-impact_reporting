"""Tests for scheduler config loading and hot-reload."""
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from pipeline.scheduler_config import (
    SchedulerConfig,
    load_config,
    set_config_path,
)


class TestSchedulerConfig(SimpleTestCase):
    def test_default_config(self):
        config = SchedulerConfig()
        self.assertEqual(config.max_concurrent_heavy, 2)
        self.assertEqual(config.memory_ceiling_pct, 75.0)
        self.assertEqual(config.cpu_ceiling_pct, 90.0)
        self.assertEqual(config.cool_down_after_failure_s, 300)
        self.assertEqual(config.starvation_boost_after_minutes, 60)
        self.assertTrue(config.eta_lookahead)

    def test_missing_file_returns_defaults(self):
        set_config_path("/nonexistent/path/config.yaml")
        config = load_config(force_reload=True)
        self.assertEqual(config.max_concurrent_heavy, 2)

    def test_load_yaml_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
host_rules:
  max_concurrent_heavy: 4
  memory_ceiling_pct: 80
  cpu_ceiling_pct: 95
  cool_down_after_failure_s: 600
stage_weights:
  crawl:
    prefer_hosts: [cloud1]
scheduling:
  starvation_boost_after_minutes: 120
  eta_lookahead: false
""")
            f.flush()
            set_config_path(f.name)
            config = load_config(force_reload=True)
            self.assertEqual(config.max_concurrent_heavy, 4)
            self.assertEqual(config.memory_ceiling_pct, 80)
            self.assertEqual(config.cpu_ceiling_pct, 95)
            self.assertEqual(config.cool_down_after_failure_s, 600)
            self.assertEqual(config.starvation_boost_after_minutes, 120)
            self.assertFalse(config.eta_lookahead)
            self.assertEqual(config.stage_weights["crawl"]["prefer_hosts"], ["cloud1"])

    def test_hot_reload_on_mtime_change(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("host_rules:\n  max_concurrent_heavy: 2\n")
            f.flush()
            set_config_path(f.name)
            config1 = load_config(force_reload=True)
            self.assertEqual(config1.max_concurrent_heavy, 2)

            # Overwrite file
            import os
            import time
            time.sleep(0.1)
            with open(f.name, "w") as f2:
                f2.write("host_rules:\n  max_concurrent_heavy: 5\n")
            # Touch to ensure mtime changes
            os.utime(f.name, None)

            config2 = load_config()
            self.assertEqual(config2.max_concurrent_heavy, 5)

    def test_invalid_yaml_returns_defaults(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("{{not valid yaml")
            f.flush()
            set_config_path(f.name)
            config = load_config(force_reload=True)
            self.assertEqual(config.max_concurrent_heavy, 2)
