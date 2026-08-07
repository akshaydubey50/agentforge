import os

import requests
import streamlit as st

API_URL = os.environ.get("RAG_API_URL", "http://localhost:8000")

st.set_page_config(page_title="RAG Production Pipeline", layout="wide")
st.title("RAG Production Pipeline — Query Dashboard")

with st.sidebar:
    st.header("Retrieval settings")
    strategy = st.selectbox(
        "Chunking strategy", ["structure_aware", "fixed_overlap", "semantic"], index=0
    )
    retrieval_mode = st.radio("Retrieval mode", ["Hybrid (dense + BM25)", "Dense only"])
    sparse_weight = 1.0 if retrieval_mode.startswith("Hybrid") else 0.0
    top_k = st.slider("Sources to use (top_k)", min_value=1, max_value=8, value=4)
    use_reranker = st.checkbox("LLM reranker", value=True)

    st.divider()
    st.header("Corpus")
    try:
        response = requests.get(f"{API_URL}/v1/documents", timeout=10)
        response.raise_for_status()
        docs = response.json()
        if not isinstance(docs, list):
            st.error(f"Unexpected response from {API_URL}/v1/documents: {docs}")
        else:
            for d in docs:
                st.caption(f"[{d['format']}] {d['title']}")
    except requests.RequestException as e:
        st.error(f"Could not reach API at {API_URL}: {e}")

question = st.text_input(
    "Ask a question about the internal engineering docs",
    placeholder="What batch size should database backfills use?",
)

if st.button("Ask", type="primary") and question:
    with st.spinner("Retrieving, generating, and verifying citations..."):
        try:
            response = requests.post(
                f"{API_URL}/v1/ask",
                json={
                    "question": question,
                    "strategy": strategy,
                    "top_k": top_k,
                    "use_reranker": use_reranker,
                    "sparse_weight": sparse_weight,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            st.error(f"Request failed: {e}")
            data = None

    if data:
        st.subheader("Answer")
        st.write(data["answer"])

        conf = data["confidence"]
        cols = st.columns(4)
        cols[0].metric("Overall confidence", f"{conf['overall']:.2f}")
        cols[1].metric("Retrieval confidence", f"{conf['retrieval_confidence']:.2f}")
        cols[2].metric("Citation coverage", f"{conf['citation_coverage']:.2f}")
        cols[3].metric("Completeness", f"{conf['completeness']:.2f}")

        if data["unsupported_claims"]:
            st.warning("Citation verifier flagged unsupported claims:")
            for claim in data["unsupported_claims"]:
                st.write(f"- **{claim['claim']}** (cited {claim['cited_source_numbers']}): {claim['reasoning']}")

        st.subheader("Retrieved sources")
        for source in data["sources"]:
            marker = "✅ cited" if source["cited"] else "— not cited"
            with st.expander(f"[{source['index']}] {source['title']} ({source['filename']}) · score={source['score']:.2f} · {marker}"):
                st.write(source["text"])
