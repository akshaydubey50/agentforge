"""
Simple Upload Dashboard - Fixed Version

Works with:
- File upload
- Auto reindex
- Query documents

Run: streamlit run src/rag/simple_upload.py
"""

import os
import shutil
from pathlib import Path

import requests
import streamlit as st

API_URL = os.environ.get("RAG_API_URL", "http://localhost:8000")
RAW_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

st.set_page_config(page_title="RAG - Upload & Query", layout="wide")
st.title("RAG Pipeline - Upload & Query")

# Sidebar
with st.sidebar:
    st.header("Settings")
    mode = st.radio("Mode", ["Upload Documents", "Query Documents"])

    if mode == "Query Documents":
        strategy = st.selectbox("Strategy", ["structure_aware", "fixed_overlap", "semantic"])
        top_k = st.slider("Top Results", 1, 8, 4)
        use_reranker = st.checkbox("Use Reranker", True)

# ============================================================================
# MODE 1: UPLOAD
# ============================================================================
if mode == "Upload Documents":
    st.header("Upload Documents")

    # Check API
    try:
        response = requests.get(f"{API_URL}/health", timeout=5)
        st.success("API Connected")
    except:
        st.error("API not running! Start: uvicorn src.rag.main:app --host 0.0.0.0 --port 8000")
        st.stop()

    # Upload section
    st.subheader("1. Upload Files")
    uploaded_files = st.file_uploader(
        "Select files to upload",
        type=["md", "txt", "pdf", "html", "htm"],
        accept_multiple_files=True
    )

    if uploaded_files:
        st.info(f"Selected {len(uploaded_files)} file(s)")

        if st.button("Upload Files", type="primary"):
            # Create directory
            RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

            with st.spinner("Uploading..."):
                success_count = 0
                error_count = 0

                for uploaded_file in uploaded_files:
                    try:
                        # Save to data/raw/
                        file_path = RAW_DATA_DIR / uploaded_file.name
                        file_path.write_bytes(uploaded_file.getbuffer())
                        success_count += 1
                        st.success(f"Uploaded: {uploaded_file.name}")
                    except Exception as e:
                        error_count += 1
                        st.error(f"Failed: {uploaded_file.name} - {e}")

                st.write(f"\nTotal: {success_count} uploaded, {error_count} failed")
                st.info("Next: Click 'Reindex Now' to index the documents")

    # Reindex section
    st.subheader("2. Reindex Documents")

    col1, col2 = st.columns([1, 3])
    with col1:
        strategy = st.selectbox("Strategy", ["all", "structure_aware", "fixed_overlap", "semantic"], key="reindex_strategy")
    with col2:
        pass

    if st.button("Reindex Now", type="secondary"):
        with st.spinner("Reindexing..."):
            try:
                response = requests.post(
                    f"{API_URL}/v1/ingest",
                    json={"strategy": strategy},
                    timeout=120
                )
                response.raise_for_status()
                data = response.json()

                st.success("Reindexing Complete!")
                for result in data.get("results", []):
                    st.write(f"**{result['strategy']}**: {result['indexed_chunks']} chunks indexed")
            except Exception as e:
                st.error(f"Reindexing failed: {e}")

    # Current documents
    st.subheader("3. Current Documents")
    try:
        response = requests.get(f"{API_URL}/v1/documents", timeout=10)
        docs = response.json()

        if docs:
            st.write(f"Total: {len(docs)} documents")
            for doc in docs:
                st.write(f"- {doc['title']} ({doc['format']})")
        else:
            st.info("No documents yet. Upload some first!")
    except Exception as e:
        st.error(f"Could not fetch documents: {e}")

# ============================================================================
# MODE 2: QUERY
# ============================================================================
else:
    st.header("Query Documents")

    # Check API
    try:
        response = requests.get(f"{API_URL}/health", timeout=5)
        st.success("API Connected")
    except:
        st.error("API not running!")
        st.stop()

    question = st.text_area("Ask a question:", height=100, placeholder="What is machine learning?")

    if st.button("Search", type="primary"):
        if not question:
            st.warning("Please enter a question")
        else:
            with st.spinner("Searching..."):
                try:
                    response = requests.post(
                        f"{API_URL}/v1/ask",
                        json={
                            "question": question,
                            "strategy": strategy,
                            "top_k": top_k,
                            "use_reranker": use_reranker,
                            "sparse_weight": 1.0
                        },
                        timeout=60
                    )
                    response.raise_for_status()
                    data = response.json()

                    # Answer
                    st.subheader("Answer")
                    st.write(data["answer"])

                    # Confidence
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Overall", f"{data['confidence']['overall']:.2f}")
                    with col2:
                        st.metric("Retrieval", f"{data['confidence']['retrieval_confidence']:.2f}")
                    with col3:
                        st.metric("Citation", f"{data['confidence']['citation_coverage']:.2f}")
                    with col4:
                        st.metric("Completeness", f"{data['confidence']['completeness']:.2f}")

                    # Unsupported claims
                    if data.get("unsupported_claims"):
                        st.warning("Unsupported Claims:")
                        for claim in data["unsupported_claims"]:
                            st.write(f"- {claim['claim']}: {claim['reasoning']}")

                    # Sources
                    st.subheader("Sources")
                    for source in data["sources"]:
                        with st.expander(f"[{source['index']}] {source['title']} (score: {source['score']:.2f})"):
                            st.write(source["text"])

                except Exception as e:
                    st.error(f"Error: {e}")
