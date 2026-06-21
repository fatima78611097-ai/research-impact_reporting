"""Document filter — classify a report's PRODUCTION TYPE from its first page (vision).

A pre-extract document gate: low-production-value "plain typed" reports (a Word doc printed
to PDF — paragraphs of black text, maybe a logo, no design) are junk for the product even when
their numbers are real, so they're filtered BEFORE extraction. Designed/professional reports
(layout, color, infographics, callout numbers, photos) pass.

Vision call: Gemini Flash-Lite (operator-validated for this coarse designed-vs-typed call).
Reuses the REST/SSM pattern from comp-metric-regression/bake-off. Returns a verdict per page
image; the caller decides keep/skip. Conservative: on an unparseable/errored reply -> KEEP
(don't silently drop a doc on a flaky call).
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.request

MODEL = "gemini-2.5-flash-lite"

PROMPT = """You are looking at the FIRST PAGE of a nonprofit's report. Judge only its PRODUCTION TYPE — how it was made, not what it says.

- "designed": a professionally designed report — uses real layout, color, graphics/infographics, large callout numbers, photos, multi-column or magazine-style design. The kind of polished impact/annual report a communications team produces.
- "plain_text": a plain typed document — essentially a word-processor file printed to PDF: paragraphs of black text under simple headings, minimal or no design. A single logo or one color in a header does NOT make it designed — if the body is plain typed text, it is plain_text.

Reply with ONLY this JSON, no prose:
{"type": "designed|plain_text", "reason": "<one short clause>"}"""


def classify_page(png_path: str, api_key: str) -> dict:
    """-> {"type": "designed"|"plain_text"|"keep", "reason": str}. 'keep' = errored, default-safe."""
    try:
        with open(png_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception as e:
        return {"type": "keep", "reason": f"no image: {type(e).__name__}"}
    body = json.dumps({
        "contents": [{"parts": [{"text": PROMPT}, {"inline_data": {"mime_type": "image/png", "data": b64}}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 400},
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=60).read())
            cand = (r.get("candidates") or [{}])[0]
            txt = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
            t = txt.strip().replace("```json", "").replace("```", "")
            j = json.loads(re.search(r"\{.*\}", t, re.S).group(0))
            typ = j.get("type")
            return {"type": typ if typ in ("designed", "plain_text") else "keep",
                    "reason": str(j.get("reason", ""))}
        except Exception as e:
            if attempt == 3:
                return {"type": "keep", "reason": f"ERR {type(e).__name__}"}   # default-safe: keep
            time.sleep(2 * (attempt + 1))


def is_low_value(verdict: dict) -> bool:
    return verdict.get("type") == "plain_text"
