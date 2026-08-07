# RAG Evaluation: Chunking Strategy Comparison

Generated 2026-08-05T12:12:58+00:00 against 50 golden Q&A cases (30 single-hop, 12 multi-hop, 8 deliberately out-of-scope).

| Strategy | Overall Correctness | Easy | Multi-hop | No-answer | Citation Precision | Citation Coverage | Retrieval Relevance | Avg Confidence | Errors |
|---|---|---|---|---|---|---|---|---|---|
| fixed_overlap | 0.98 | 0.983 | 0.958 | 1.0 | 0.984 | 0.816 | 0.976 | 0.797 | 0 |
| structure_aware | 0.967 | 0.983 | 0.904 | 1.0 | 0.988 | 0.779 | 0.964 | 0.788 | 0 |
| semantic | 0.966 | 0.97 | 0.931 | 1.0 | 0.992 | 0.74 | 0.976 | 0.795 | 0 |

## Metric Definitions

- **Correctness**: LLM-judge score (0-1) of whether the answer conveys the expected facts for the question, or correctly declines for out-of-scope questions.
- **Citation Precision**: of the claims the answer cited, what fraction were actually supported by their cited source (catches mis-citation).
- **Citation Coverage**: of all factual claims made (cited + uncited), what fraction were both cited and supported.
- **Retrieval Relevance**: for in-scope questions, what fraction of the expected source documents were actually retrieved and cited (excludes out-of-scope questions, which have no expected source).