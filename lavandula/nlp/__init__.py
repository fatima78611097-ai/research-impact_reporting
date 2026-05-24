"""Statistical NLP extraction and archetype discovery (Spec 0049).

Pure Python module — no Django dependency. Provides:
- Term extraction via spaCy (noun phrases, NER, POS-pattern n-grams)
- C-value multi-word term scoring
- TF-IDF keyness computation (within-vertical vs cross-corpus)
- FP-Growth archetype discovery with hierarchical clustering
"""
