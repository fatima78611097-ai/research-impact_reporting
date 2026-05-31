"""Unit tests for Spec 0058 — pre-parse triage, conditional-OCR decision, and
the bounded pipeline-options builder.

These are the CI (deterministic-fixture) tier from spec §6.8. They exercise the
PURE functions only — no docling, no DB, no GPU — so they import the module
directly (docling imports in chunking.py are lazy, inside functions).
"""
from __future__ import annotations

from lavandula.parse import config
from lavandula.parse.chunking import (
    OcrSignal,
    ParseOptions,
    build_parse_options,
    choose_detector_source,
    compute_triage,
    decide_skip_ocr,
)


class TestComputeTriage:
    """spec §3.1 — mb/page poison profile + absolute ratio-independent ceilings."""

    def test_poison_doc_profile_downgrades(self):
        # e038a9e75317ff86: 5.6 MB / 4 pg / 19-char text layer -> 1.4 MB/page, thin.
        out = compute_triage(5_600_000, 4, 19)
        assert out["downgrade"] is True
        assert "poison_profile" in out["reasons"]
        assert round(out["mb_per_page"], 2) == 1.4

    def test_high_mb_per_page_single_page_downgrades(self):
        # 658d9b89...: ~46.9 MB/page, thin text. Ratio fires (file < 50 MB cap).
        out = compute_triage(46_900_000, 1, 0)
        assert out["downgrade"] is True
        assert "poison_profile" in out["reasons"]
        assert "abs_file_size" not in out["reasons"]  # 46.9 MB < 50 MB cap

    def test_normal_text_native_doc_not_downgraded(self):
        # 1.5 MB / 30 pg / 5000-char text -> 0.05 MB/page, healthy text.
        out = compute_triage(1_500_000, 30, 5000)
        assert out["downgrade"] is False
        assert out["reasons"] == []

    def test_image_heavy_but_text_present_not_poison(self):
        # Image-heavy ratio but a healthy text layer -> NOT the poison profile
        # (requires BOTH high ratio AND thin text).
        out = compute_triage(10_000_000, 4, 5000)  # 2.5 MB/page but text=5000
        assert "poison_profile" not in out["reasons"]
        assert out["downgrade"] is False

    def test_absolute_file_size_cap_fires_regardless_of_ratio(self):
        # 60 MB across 100 pages -> ratio only 0.6 MB/page and text healthy, but
        # the absolute 50 MB ceiling still downgrades (ratio-bypass defense).
        out = compute_triage(60_000_000, 100, 5000)
        assert out["downgrade"] is True
        assert "abs_file_size" in out["reasons"]
        assert "poison_profile" not in out["reasons"]

    def test_absolute_page_cap_fires(self):
        out = compute_triage(5_000_000, 250, 5000)  # ratio tiny, text healthy
        assert out["downgrade"] is True
        assert "abs_page_count" in out["reasons"]

    def test_ratio_bypass_blank_pages_still_caught_by_abs_size(self):
        # Appended blank pages drop the ratio (huge file / many pages) but the
        # 50 MB absolute cap still fires.
        out = compute_triage(80_000_000, 500, 0)  # 0.16 MB/page
        assert out["downgrade"] is True
        assert "abs_file_size" in out["reasons"]

    def test_missing_page_count_skips_ratio_but_keeps_abs_size(self):
        out = compute_triage(60_000_000, None, 10)
        assert out["mb_per_page"] is None
        assert "poison_profile" not in out["reasons"]
        assert "abs_file_size" in out["reasons"]
        assert out["downgrade"] is True

    def test_missing_everything_no_downgrade(self):
        out = compute_triage(None, None, None)
        assert out["downgrade"] is False
        assert out["mb_per_page"] is None

    def test_threshold_is_strict_greater_than(self):
        # Exactly at 1.0 MB/page is NOT > 1.0 -> not poison even with thin text.
        out = compute_triage(1_000_000, 1, 0)
        assert "poison_profile" not in out["reasons"]


class TestChooseDetectorSource:
    """spec §3.4 / Codex determinism guard — backfill-completeness gate."""

    def test_above_threshold_uses_pdftotext(self):
        assert choose_detector_source(0.995) == "pdftotext"

    def test_exactly_at_threshold_uses_pdftotext(self):
        assert choose_detector_source(config.OCR_DETECTOR_BACKFILL_MIN) == "pdftotext"

    def test_below_threshold_uses_first_page_text(self):
        assert choose_detector_source(0.78) == "first_page_text"

    def test_none_uses_first_page_text(self):
        assert choose_detector_source(None) == "first_page_text"


class TestDecideSkipOcr:
    """spec §3.4 — keep OCR unless an embedded text layer is confidently present."""

    def test_pdftotext_text_native_healthy_skips(self):
        sig = OcrSignal(text_source="text_native", pdftotext_char_count=5000)
        assert decide_skip_ocr(sig, detector_source="pdftotext") is True

    def test_pdftotext_text_native_thin_keeps(self):
        sig = OcrSignal(text_source="text_native", pdftotext_char_count=50)
        assert decide_skip_ocr(sig, detector_source="pdftotext") is False

    def test_pdftotext_scanned_keeps(self):
        sig = OcrSignal(text_source="scanned", pdftotext_char_count=0)
        assert decide_skip_ocr(sig, detector_source="pdftotext") is False

    def test_pdftotext_failed_keeps(self):
        sig = OcrSignal(text_source="pdftotext_failed", pdftotext_char_count=0)
        assert decide_skip_ocr(sig, detector_source="pdftotext") is False

    def test_pdftotext_char_count_healthy_without_text_source_skips(self):
        # First-parse case: no documents.text_source row yet, but the corpus-wide
        # 0060 backfill gave us a healthy char_count -> skip OCR.
        sig = OcrSignal(text_source=None, pdftotext_char_count=5000)
        assert decide_skip_ocr(sig, detector_source="pdftotext") is True

    def test_pdftotext_char_count_thin_without_text_source_keeps(self):
        sig = OcrSignal(text_source=None, pdftotext_char_count=10)
        assert decide_skip_ocr(sig, detector_source="pdftotext") is False

    def test_pdftotext_source_but_no_row_falls_back_to_first_page_text(self):
        # detector is pdftotext but this doc has no 0060 row (text_source None):
        # precedence falls through to the first_page_text rule.
        sig = OcrSignal(text_source=None, first_page_text_len=5000)
        assert decide_skip_ocr(sig, detector_source="pdftotext") is True

    def test_pdftotext_source_no_row_thin_first_page_keeps(self):
        sig = OcrSignal(text_source=None, first_page_text_len=10)
        assert decide_skip_ocr(sig, detector_source="pdftotext") is False

    def test_first_page_text_source_healthy_skips(self):
        sig = OcrSignal(first_page_text_len=5000)
        assert decide_skip_ocr(sig, detector_source="first_page_text") is True

    def test_first_page_text_source_empty_keeps(self):
        sig = OcrSignal(first_page_text_len=None)
        assert decide_skip_ocr(sig, detector_source="first_page_text") is False

    def test_disagreement_pdftotext_scanned_wins_over_long_first_page(self):
        # pdftotext says scanned; even a long first_page_text must not skip OCR.
        sig = OcrSignal(text_source="scanned", pdftotext_char_count=0, first_page_text_len=9000)
        assert decide_skip_ocr(sig, detector_source="pdftotext") is False

    def test_all_signals_absent_keeps_ocr(self):
        assert decide_skip_ocr(OcrSignal(), detector_source="pdftotext") is False
        assert decide_skip_ocr(OcrSignal(), detector_source="first_page_text") is False


class TestBuildParseOptions:
    """spec §3.4 / §6.8 — options builder produces FAST + capped scale + OCR flag."""

    def test_skip_ocr_sets_do_ocr_false(self):
        opts = build_parse_options(skip_ocr=True, downgrade=False)
        assert opts.do_ocr is False

    def test_keep_ocr_sets_do_ocr_true(self):
        opts = build_parse_options(skip_ocr=False, downgrade=False)
        assert opts.do_ocr is True

    def test_downgrade_uses_lower_images_scale(self):
        opts = build_parse_options(skip_ocr=False, downgrade=True)
        assert opts.images_scale == config.IMAGES_SCALE_DOWNGRADE

    def test_normal_uses_images_scale_cap(self):
        opts = build_parse_options(skip_ocr=False, downgrade=False)
        assert opts.images_scale == config.IMAGES_SCALE_CAP

    def test_fast_override_sets_fast_mode(self):
        opts = build_parse_options(skip_ocr=False, downgrade=False, table_mode_fast=True)
        assert opts.table_mode_fast is True
        assert opts.do_cell_matching is True

    def test_default_fast_follows_config(self):
        opts = build_parse_options(skip_ocr=False, downgrade=False)
        assert opts.table_mode_fast == config.TABLEFORMER_FAST

    def test_document_timeout_threads_through(self):
        opts = build_parse_options(skip_ocr=False, downgrade=False, document_timeout=180.0)
        assert opts.document_timeout == 180.0

    def test_max_num_pages_default_is_absolute_ceiling(self):
        opts = build_parse_options(skip_ocr=False, downgrade=False)
        assert opts.max_num_pages == config.MAX_NUM_PAGES

    def test_reasons_are_tupleized(self):
        opts = build_parse_options(
            skip_ocr=False, downgrade=True, reasons=["poison_profile", "abs_file_size"]
        )
        assert opts.reasons == ("poison_profile", "abs_file_size")
        assert isinstance(opts.reasons, tuple)

    def test_is_frozen_dataclass(self):
        opts = build_parse_options(skip_ocr=False, downgrade=False)
        assert isinstance(opts, ParseOptions)
        import dataclasses

        try:
            opts.do_ocr = True  # type: ignore[misc]
            raised = False
        except dataclasses.FrozenInstanceError:
            raised = True
        assert raised
