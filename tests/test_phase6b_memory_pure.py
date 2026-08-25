from agentsys.memory.curation import normalize_content, normalized_hash


def test_phase6b_normalized_hash_dedupes_trivial_content_variants():
    first = " Postgres is the durable source of truth. "
    second = "postgres is the durable source of truth"

    assert normalize_content(first) == normalize_content(second)
    assert normalized_hash(first) == normalized_hash(second)


def test_phase6b_normalized_hash_preserves_different_memory_content():
    assert normalized_hash("Postgres is durable truth") != normalized_hash("Redis is durable truth")
