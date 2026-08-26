from agentsys import context


def test_phase6d_known_and_unknown_model_windows_are_deterministic():
    assert context.model_context_window("openai/gpt-4o-mini") == 128_000
    assert context.model_context_window("vendor/new-model") == context.UNKNOWN_CONTEXT_WINDOW_TOKENS


def test_phase6d_representative_benchmark_preserves_quality_flags():
    results = context.run_representative_context_benchmark()

    assert results
    assert all(row["critical_evidence_preserved"] for row in results)
    assert all(row["mandatory_context_preserved"] for row in results)


def test_phase6d_representative_benchmark_never_exceeds_baseline():
    results = context.run_representative_context_benchmark()

    assert all(row["optimized_tokens"] <= row["baseline_tokens"] for row in results)
