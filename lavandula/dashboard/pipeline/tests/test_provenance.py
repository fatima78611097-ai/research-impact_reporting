"""Tests for provenance writer and file reader."""
import json
import os
import tempfile

from django.test import SimpleTestCase

from pipeline.provenance import (
    VALID_STATUSES,
    ProvenanceSecurityError,
    read_provenance_file,
)


class TestValidStatuses(SimpleTestCase):
    def test_expected_statuses(self):
        self.assertEqual(
            VALID_STATUSES,
            {"not_started", "in_progress", "completed", "failed", "not_applicable"},
        )


class TestReadProvenanceFile(SimpleTestCase):
    def test_read_valid_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "123.jsonl")
            with open(file_path, "w") as f:
                f.write(json.dumps({"ein": "123456789", "status": "completed"}) + "\n")
                f.write(json.dumps({"ein": "987654321", "status": "failed"}) + "\n")

            import pipeline.provenance as prov
            orig = prov.PROVENANCE_BASE_DIR
            prov.PROVENANCE_BASE_DIR = tmpdir
            try:
                outcomes = read_provenance_file(123)
                self.assertEqual(len(outcomes), 2)
                self.assertEqual(outcomes[0], ("123456789", "completed"))
                self.assertEqual(outcomes[1], ("987654321", "failed"))
            finally:
                prov.PROVENANCE_BASE_DIR = orig

    def test_skips_invalid_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "456.jsonl")
            with open(file_path, "w") as f:
                f.write(json.dumps({"ein": "111111111", "status": "invalid_status"}) + "\n")
                f.write(json.dumps({"ein": "222222222", "status": "completed"}) + "\n")

            import pipeline.provenance as prov
            orig = prov.PROVENANCE_BASE_DIR
            prov.PROVENANCE_BASE_DIR = tmpdir
            try:
                outcomes = read_provenance_file(456)
                self.assertEqual(len(outcomes), 1)
                self.assertEqual(outcomes[0], ("222222222", "completed"))
            finally:
                prov.PROVENANCE_BASE_DIR = orig

    def test_skips_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "789.jsonl")
            with open(file_path, "w") as f:
                f.write("not json\n")
                f.write(json.dumps({"ein": "333333333", "status": "completed"}) + "\n")

            import pipeline.provenance as prov
            orig = prov.PROVENANCE_BASE_DIR
            prov.PROVENANCE_BASE_DIR = tmpdir
            try:
                outcomes = read_provenance_file(789)
                self.assertEqual(len(outcomes), 1)
            finally:
                prov.PROVENANCE_BASE_DIR = orig

    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a symlink pointing outside the base dir
            import pipeline.provenance as prov
            orig = prov.PROVENANCE_BASE_DIR
            prov.PROVENANCE_BASE_DIR = tmpdir

            try:
                # Manually override the file path logic
                evil_path = os.path.join(tmpdir, "..", "etc", "passwd")
                # The function computes path from job_id, so no direct injection.
                # But test the realpath check indirectly:
                # Create a symlink inside base dir pointing elsewhere
                link_path = os.path.join(tmpdir, "999.jsonl")
                target = "/etc/hostname"
                if os.path.exists(target):
                    os.symlink(target, link_path)
                    with self.assertRaises(ProvenanceSecurityError):
                        read_provenance_file(999)
            finally:
                prov.PROVENANCE_BASE_DIR = orig

    def test_file_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import pipeline.provenance as prov
            orig = prov.PROVENANCE_BASE_DIR
            prov.PROVENANCE_BASE_DIR = tmpdir
            try:
                with self.assertRaises(FileNotFoundError):
                    read_provenance_file(99999)
            finally:
                prov.PROVENANCE_BASE_DIR = orig

    def test_skips_empty_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "100.jsonl")
            with open(file_path, "w") as f:
                f.write("\n")
                f.write(json.dumps({"ein": "444444444", "status": "completed"}) + "\n")
                f.write("\n")

            import pipeline.provenance as prov
            orig = prov.PROVENANCE_BASE_DIR
            prov.PROVENANCE_BASE_DIR = tmpdir
            try:
                outcomes = read_provenance_file(100)
                self.assertEqual(len(outcomes), 1)
            finally:
                prov.PROVENANCE_BASE_DIR = orig
