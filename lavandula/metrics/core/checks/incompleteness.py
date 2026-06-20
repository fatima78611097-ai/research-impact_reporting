"""Incompleteness — FLAG a bare generic people-count with no action ("838 individuals").

Per the metric definition (theory-of-change guidance): a usable metric needs a quantity,
a subject, AND an action/relationship. A generic people-noun counted with no verb and no
context fails. Ported from the spike's incomplete_gate (spaCy head-noun rule), tuned to NOT
touch financial totals or specific counts (verified: 6/296 on the sample).

This FLAGS (non-blocking) — it does not quarantine. Safe by construction: never fabricates.
"""
from __future__ import annotations

from ..types import Metric, CheckResult

# generic people/unit nouns that need an action or context to be a real metric
GENERIC = {"individual", "individuals", "people", "person", "persons", "household", "households",
           "participant", "participants", "client", "clients", "member", "members",
           "resident", "residents", "attendee", "attendees"}

_nlp = None


def _model():
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_sm")
    return _nlp


def incompleteness(m: Metric) -> CheckResult:
    doc = _model()(m.statement or "")
    if any(t.pos_ == "VERB" for t in doc):
        return CheckResult()                              # has an action
    if any(t.pos_ == "ADP" for t in doc):
        return CheckResult()                              # "for/through/of ..." carries context
    nouns = [t for t in doc if t.pos_ == "NOUN"]
    if not nouns:
        return CheckResult()
    if nouns[-1].lemma_.lower() in GENERIC:               # the thing being counted is generic
        return CheckResult("flag", "bare count — no action or context (generic people-noun)", flag="incomplete")
    return CheckResult()
