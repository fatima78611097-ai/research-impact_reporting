"""Extract step: run the metric prompt over a document's tagged text -> raw Metric records.

Lifts the prompt + DeepSeek call from nlp/llm_extract (deepseek-chat). Returns the model's
own text as `Metric.statement` — no construction. Implemented in Phase 1.
"""
def extract(tagged_text: str):
    raise NotImplementedError("Phase 1")
