"""Tests for the pipeline_protocol emission library."""
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

# Paths needed for subprocess imports
_DASHBOARD_ROOT = str(Path(__file__).resolve().parents[2])
_PROJECT_ROOT = str(Path(__file__).resolve().parents[4])


class TestProtocolEmission(SimpleTestCase):
    """Test emission by running in a subprocess with fd 3 properly set up."""

    def _run_emission_script(self, code: str) -> str:
        """Run Python code in a subprocess with fd 3 piped back to us."""
        r_fd, w_fd = os.pipe()
        script = (
            f"import os, sys\n"
            f"sys.path.insert(0, {_DASHBOARD_ROOT!r})\n"
            f"sys.path.insert(0, {_PROJECT_ROOT!r})\n"
            f"os.dup2(int(sys.argv[1]), 3)\n"
            f"os.close(int(sys.argv[1]))\n"
            f"import lavandula.pipeline_protocol as proto\n"
            f"{code}\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(w_fd)],
            pass_fds=(w_fd,),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        os.close(w_fd)
        proc.wait()
        data = os.read(r_fd, 65536)
        os.close(r_fd)
        if proc.returncode != 0:
            self.fail(f"Subprocess failed: {proc.stderr.read().decode()}")
        return data.decode("utf-8")

    def test_emit_progress_basic(self):
        output = self._run_emission_script("proto.emit_progress(current=500, total=3589)")
        self.assertIn("PROGRESS:", output)
        self.assertIn("current=500", output)
        self.assertIn("total=3589", output)

    def test_emit_progress_with_extra_kwargs(self):
        output = self._run_emission_script('proto.emit_progress(current=100, rate="437")')
        self.assertIn("current=100", output)
        self.assertIn("rate=437", output)

    def test_emit_error(self):
        output = self._run_emission_script('proto.emit_error("flush_failure", "3 unresolved")')
        self.assertIn("ERROR:", output)
        self.assertIn("class=flush_failure", output)
        self.assertIn("detail=3 unresolved", output)

    def test_emit_error_strips_newlines(self):
        output = self._run_emission_script(r'proto.emit_error("test", "line1\nline2\nline3")')
        self.assertIn("line1 line2 line3", output)
        lines = [l for l in output.strip().split("\n") if l.startswith("ERROR:")]
        self.assertEqual(len(lines), 1)

    def test_emit_summary(self):
        output = self._run_emission_script(
            "proto.emit_summary(duration_s=29376, records_processed=3537)"
        )
        self.assertIn("SUMMARY:", output)
        self.assertIn("duration_s=29376", output)
        self.assertIn("records_processed=3537", output)

    def test_emit_warning(self):
        output = self._run_emission_script('proto.emit_warning("slow", "API took 30s")')
        self.assertIn("WARNING:", output)
        self.assertIn("class=slow", output)

    def test_fd3_unavailable_noop(self):
        """When fd 3 is not available, emit functions are no-ops (no crash)."""
        script = (
            f"import os, sys\n"
            f"sys.path.insert(0, {_DASHBOARD_ROOT!r})\n"
            f"sys.path.insert(0, {_PROJECT_ROOT!r})\n"
            f"import lavandula.pipeline_protocol as proto\n"
            f"proto.emit_progress(current=1)\n"
            f"proto.emit_error('test', 'detail')\n"
            f"proto.emit_summary(x=1)\n"
            f"print('OK')\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = proc.communicate()
        self.assertEqual(proc.returncode, 0, f"Failed: {stderr.decode()}")
        self.assertIn(b"OK", stdout)
