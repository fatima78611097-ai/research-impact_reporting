"""Tests for augmented prompt builder (Spec 0035 Phase 4)."""
from __future__ import annotations

import pytest


class TestBuildAugmentedUserMessage:
    def test_basic_structure(self):
        from lavandula.reports.classify import build_augmented_user_message
        msg = build_augmented_user_message(
            pages_text="--- PAGE 1 ---\nHello world",
            first_page_text="Hello world",
            page_count=5,
            file_size_bytes=4_500_000,
            nonce="test1234",
        )
        assert "<document_metadata>" in msg
        assert "Page count: 5" in msg
        assert "4.3 MB" in msg
        assert "</document_metadata>" in msg
        assert "<untrusted_document>" in msg
        assert "</untrusted_document>" in msg
        assert "[test1234]" in msg

    def test_metadata_separate_from_untrusted(self):
        from lavandula.reports.classify import build_augmented_user_message
        msg = build_augmented_user_message(
            pages_text="--- PAGE 1 ---\nText",
            first_page_text="Text",
            page_count=10,
            file_size_bytes=1024,
            nonce="abc",
        )
        meta_start = msg.index("<document_metadata>")
        meta_end = msg.index("</document_metadata>")
        untrusted_start = msg.index("<untrusted_document>")
        assert meta_end < untrusted_start

    def test_url_path_in_untrusted(self):
        from lavandula.reports.classify import build_augmented_user_message
        msg = build_augmented_user_message(
            pages_text=None,
            first_page_text="Some text",
            url_path="https://example.org/reports/annual-2024.pdf",
            nonce="abc",
        )
        assert "URL path:" in msg
        untrusted_start = msg.index("<untrusted_document>")
        url_pos = msg.index("URL path:")
        assert url_pos > untrusted_start

    def test_pdf_creator_in_untrusted(self):
        from lavandula.reports.classify import build_augmented_user_message
        msg = build_augmented_user_message(
            pages_text=None,
            first_page_text="Some text",
            pdf_creator="Adobe InDesign 2024",
            nonce="abc",
        )
        assert "PDF creator: Adobe InDesign 2024" in msg
        untrusted_start = msg.index("<untrusted_document>")
        creator_pos = msg.index("PDF creator:")
        assert creator_pos > untrusted_start

    def test_fallback_to_first_page_text(self):
        from lavandula.reports.classify import build_augmented_user_message
        msg = build_augmented_user_message(
            pages_text=None,
            first_page_text="First page content",
            nonce="abc",
        )
        assert "First page content" in msg

    def test_pages_text_preferred_over_first_page(self):
        from lavandula.reports.classify import build_augmented_user_message
        msg = build_augmented_user_message(
            pages_text="--- PAGE 1 ---\nMulti page content",
            first_page_text="Single page content",
            nonce="abc",
        )
        assert "Multi page content" in msg
        assert "Single page content" not in msg

    def test_xml_tags_stripped(self):
        from lavandula.reports.classify import build_augmented_user_message
        msg = build_augmented_user_message(
            pages_text="Hello <script>alert(1)</script> World",
            first_page_text="test",
            nonce="abc",
        )
        assert "<script>" not in msg
        assert "</script>" not in msg
        assert "Hello" in msg
        assert "World" in msg

    def test_pdf_creator_sanitized(self):
        from lavandula.reports.classify import build_augmented_user_message
        long_creator = "A" * 200
        msg = build_augmented_user_message(
            pages_text=None,
            first_page_text="text",
            pdf_creator=long_creator,
            nonce="abc",
        )
        creator_in_msg = msg.split("PDF creator: ")[1].split("\n")[0]
        assert len(creator_in_msg) <= 100

    def test_nonce_in_page_markers(self):
        from lavandula.reports.classify import build_augmented_user_message
        msg = build_augmented_user_message(
            pages_text="--- PAGE 1 ---\nPage one\n--- PAGE 2 ---\nPage two",
            first_page_text="",
            nonce="deadbeef",
        )
        assert "--- PAGE 1 [deadbeef] ---" in msg
        assert "--- PAGE 2 [deadbeef] ---" in msg

    def test_no_metadata_when_none_provided(self):
        from lavandula.reports.classify import build_augmented_user_message
        msg = build_augmented_user_message(
            pages_text=None,
            first_page_text="Just text",
            nonce="abc",
        )
        assert "<document_metadata>" not in msg


class TestExtractUrlPath:
    def test_http_url(self):
        from lavandula.reports.classify import _extract_url_path
        assert _extract_url_path("https://example.org/reports/annual.pdf") == "/reports/annual.pdf"

    def test_non_http_scheme(self):
        from lavandula.reports.classify import _extract_url_path
        assert _extract_url_path("ftp://example.org/file.pdf") == ""

    def test_empty_string(self):
        from lavandula.reports.classify import _extract_url_path
        assert _extract_url_path("") == ""

    def test_angle_brackets_stripped(self):
        from lavandula.reports.classify import _extract_url_path
        result = _extract_url_path("https://example.org/<script>")
        assert "<" not in result
        assert ">" not in result


class TestFormatFileSize:
    def test_bytes(self):
        from lavandula.reports.classify import _format_file_size
        assert _format_file_size(500) == "500 bytes"

    def test_kilobytes(self):
        from lavandula.reports.classify import _format_file_size
        result = _format_file_size(2048)
        assert "KB" in result

    def test_megabytes(self):
        from lavandula.reports.classify import _format_file_size
        result = _format_file_size(4_500_000)
        assert "MB" in result

    def test_none(self):
        from lavandula.reports.classify import _format_file_size
        assert _format_file_size(None) is None


class TestClassifyV3Augmented:
    def test_augmented_mode_sends_metadata(self):
        from lavandula.reports.classify import classify_first_page_v3

        captured_kwargs = {}

        class _StubClient:
            class messages:
                @staticmethod
                def create(**kwargs):
                    captured_kwargs.update(kwargs)
                    return type("R", (), {
                        "content": [type("B", (), {
                            "type": "tool_use",
                            "name": "record_classification",
                            "input": {
                                "material_type": "annual_report",
                                "confidence": 0.95,
                                "reasoning": "annual",
                            },
                        })],
                        "usage": type("U", (), {"input_tokens": 100, "output_tokens": 50}),
                    })()

        from lavandula.nonprofits.definition_loader import load_definition, _clear_cache
        _clear_cache()
        definition = load_definition("corpus_reports")
        assert definition.context_mode == "multipage"

        result = classify_first_page_v3(
            "First page only",
            client=_StubClient(),
            definition=definition,
            pages_text="--- PAGE 1 ---\nMulti page",
            page_count=5,
            file_size_bytes=1024,
        )
        user_msg = captured_kwargs["messages"][0]["content"]
        assert "<document_metadata>" in user_msg
        assert "Page count: 5" in user_msg

    def test_single_page_mode_no_metadata(self):
        from lavandula.reports.classify import classify_first_page_v3
        from lavandula.nonprofits.definition_loader import ClassifierDefinition, CategoryDef

        captured_kwargs = {}

        class _StubClient:
            class messages:
                @staticmethod
                def create(**kwargs):
                    captured_kwargs.update(kwargs)
                    return type("R", (), {
                        "content": [type("B", (), {
                            "type": "tool_use",
                            "name": "record_classification",
                            "input": {
                                "material_type": "annual_report",
                                "confidence": 0.9,
                                "reasoning": "test",
                            },
                        })],
                        "usage": type("U", (), {"input_tokens": 100, "output_tokens": 50}),
                    })()

        defn = ClassifierDefinition(
            name="test", version=1, description="test",
            source_taxonomy=None, output_columns=["material_type"],
            system_prompt="Test", categories=[CategoryDef(id="annual_report", group="reports", body="test")],
            guidelines="", event_types=[],
            tool_schema={
                "type": "function",
                "function": {
                    "name": "record_classification",
                    "description": "test",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "material_type": {"type": "string", "enum": ["annual_report"]},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "reasoning": {"type": "string"},
                        },
                        "required": ["material_type", "confidence", "reasoning"],
                    },
                },
            },
            context_mode="single_page",
        )

        classify_first_page_v3(
            "First page only",
            client=_StubClient(),
            definition=defn,
        )
        user_msg = captured_kwargs["messages"][0]["content"]
        assert "<document_metadata>" not in user_msg
