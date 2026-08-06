GENERATION_PROMPT = """Answer the question using ONLY the numbered sources below. \
Cite every factual claim inline with bracketed source numbers, e.g. [1] or [2][3] for a claim \
drawn from multiple sources. Do not cite a source for a sentence it doesn't actually support.

If the sources do not contain enough information to fully answer the question, say so \
explicitly — state what the sources do cover and what they leave unanswered, rather than \
filling the gap with outside knowledge or a guess.

Question: {query}

Sources:
{sources}

Answer (with inline bracketed citations):"""


VERIFICATION_PROMPT = """You are a fact-checking judge. Given a question, an answer that cites \
numbered sources, and the sources themselves, verify the answer's citations.

For every distinct factual claim in the answer that carries a bracketed citation, record the \
claim, which source numbers it cited, whether those sources actually support the claim \
(supported=true only if the cited source(s) genuinely state or clearly imply the claim), and a \
one-sentence reasoning.

Separately, list any factual claims in the answer that have NO bracketed citation at all \
(uncited_factual_claims) — this excludes meta-statements like "the sources don't cover X".

Finally, rate completeness_score 1-5: how fully the answer addresses the question given what \
the sources actually contain (5 = fully answered; 1 = barely addresses it). An honest "the \
sources don't cover this" is NOT low completeness if the sources genuinely don't cover it.

Question: {query}

Answer to verify:
{answer}

Sources:
{sources}
"""
