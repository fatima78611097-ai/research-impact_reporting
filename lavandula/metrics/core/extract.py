"""Extract step: a parsed document -> the model's raw metrics.

`prepare` renders the parsed doc to tagged text + a marker->page/bbox/text map — shared by
BOTH harnesses (dev and prod read the same parsed docs the same way; only WHICH docs differs,
which is the input adapter's job). `extract` runs the metric prompt (deepseek-chat) and returns
the model's own text in `metric_text` — no construction, no rewriting.

Lifted from the spike's regen_1; the prompt + DeepSeek call stay in nlp/llm_extract (one source).
"""
from __future__ import annotations

from lavandula.nlp.marker_render import render_tagged
from lavandula.nlp.llm_extract import _METRICS_PROMPT, call_deepseek


def prepare(conn, content_sha256: str):
    """parsed doc -> (tagged_text for the LLM, idmap marker->{page,bbox,text}). One render pass."""
    r = render_tagged(conn, content_sha256)
    return r.tagged_text, r.idmap


def extract(tagged_text: str, api_key: str, http_client) -> list[dict]:
    """Run the metric prompt over tagged text -> list of raw model-metric dicts.

    Each dict carries: metric_text (the model's own sentence), metric_value, unit, tier,
    source_snippet, value_ref, subject_ref. Returns [] on a non-list reply.
    """
    raw, _pt, _ct = call_deepseek(api_key, tagged_text, http_client, _METRICS_PROMPT)
    return raw if isinstance(raw, list) else []
