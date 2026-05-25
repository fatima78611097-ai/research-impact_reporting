"""Boilerplate term lists and entity label filters for NLP extraction."""

BOILERPLATE_TERMS: frozenset[str] = frozenset({
    # Generic nonprofit
    "community",
    "mission",
    "impact",
    "stakeholders",
    "board of directors",
    "fiscal year",
    "annual report",
    "strategic plan",
    # Financial/IRS
    "form 990",
    "tax-exempt",
    "gross receipts",
    "net assets",
})

ENTITY_LABELS_KEEP: frozenset[str] = frozenset({
    "ORG",
    "PRODUCT",
    "EVENT",
    "WORK_OF_ART",
})
