# Qwen residual-mosaic eval — QUEUED (runs after P20 adjudication)

**Question:** can Qwen2.5-VL-7B on the g6.2xlarge (L4 24GB) correctly pair number↔label on the
dense/mosaic pages the geometry re-grouper abstains on — the residual class nothing else fixes?

**Eval set (ground truth already in hand or arriving):**
- The 44 dev-corpus residual mispairs + regroup-abstain pages (Opus page-truth adjudications exist).
- P20 additions: pages behind the adjudicated flags + abstain-with-multiple-numbers pages from the
  600-doc sample (adjudication produces their truth as a side effect).

**Method:**
1. Render each eval page to PNG (pdftoppm, existing pattern).
2. Deploy Qwen2.5-VL-7B-Instruct on the g6 (reuse the VL2/parse_documents g6 deploy path; int4 or bf16
   — VL2 work showed int4 == bf16 on L4).
3. Prompt: "list every hero number on this page with the caption it belongs to" → JSON pairs.
4. Score against ground truth: pairing accuracy on exactly the abstain/mosaic class
   (plus a clean-grid control to confirm no weirdness).

**Decision rule:** if pairing accuracy on the residual class is high (bar to set with operator —
suggest ≥90% on mosaics), the national architecture locks as:
CPU geometry (clean grids, free) → g6 small-VLM (dense residue only) → prose never routed.
The 600-doc sample also yields the routing-volume estimate (abstain pages/doc) → GPU cost projection.

**Cost:** a few dozen→hundred page inferences on the g6; ~an hour of GPU.
**Operator action needed:** g6 spin-up approval when we get there.
