"""Tests for the protocol reader (orchestrator side)."""
import os
import time

from django.test import SimpleTestCase

from pipeline.protocol_reader import ProtocolReader, create_protocol_pipe


class TestProtocolReader(SimpleTestCase):
    def _run_reader(self, write_data: bytes, **callbacks):
        """Helper: create pipe, start reader, write data, close, wait for processing."""
        r_fd, w_fd = create_protocol_pipe()
        reader = ProtocolReader(r_fd, **callbacks)
        reader.start()
        os.write(w_fd, write_data)
        os.close(w_fd)
        # Wait for the reader thread to finish (pipe closed = EOF = thread exits)
        reader._thread.join(timeout=2)
        return reader

    def test_parses_progress(self):
        received = []
        self._run_reader(
            b"PROGRESS: current=100 total=500\n",
            on_progress=lambda p: received.append(p),
        )
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["current"], 100)
        self.assertEqual(received[0]["total"], 500)

    def test_parses_error(self):
        received = []
        reader = self._run_reader(
            b"ERROR: class=timeout detail=Connection timed out after 30s\n",
            on_error=lambda e: received.append(e),
        )
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["error_class"], "timeout")
        self.assertIn("Connection timed out", received[0]["error_detail"])
        self.assertEqual(len(reader.errors), 1)

    def test_parses_summary(self):
        reader = self._run_reader(
            b"SUMMARY: duration_s=100 records_processed=500\n",
        )
        self.assertIsNotNone(reader.last_summary)
        self.assertEqual(reader.last_summary["duration_s"], 100)
        self.assertEqual(reader.last_summary["records_processed"], 500)

    def test_handles_malformed_lines(self):
        received = []
        self._run_reader(
            b"this is not a protocol line\nPROGRESS: current=42\nalso garbage\n",
            on_progress=lambda p: received.append(p),
        )
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["current"], 42)

    def test_handles_empty_lines(self):
        received = []
        self._run_reader(
            b"\n\n\nPROGRESS: current=1\n\n",
            on_progress=lambda p: received.append(p),
        )
        self.assertEqual(len(received), 1)

    def test_multiple_events_in_sequence(self):
        progress_events = []
        error_events = []
        self._run_reader(
            b"PROGRESS: current=10 total=100\n"
            b"PROGRESS: current=50 total=100\n"
            b"ERROR: class=warning detail=slow\n"
            b"PROGRESS: current=100 total=100\n",
            on_progress=lambda p: progress_events.append(p),
            on_error=lambda e: error_events.append(e),
        )
        self.assertEqual(len(progress_events), 3)
        self.assertEqual(len(error_events), 1)

    def test_stop_idempotent(self):
        r_fd, w_fd = create_protocol_pipe()
        reader = ProtocolReader(r_fd)
        reader.start()
        os.close(w_fd)
        reader.stop()
        reader.stop()  # Should not raise
