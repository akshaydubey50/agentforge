"""
Enhanced RAG Dashboard with Document Upload

Features:
- Upload multiple documents (drag & drop)
- Automatic reindexing
- Bulk management
- Progress tracking
- Query with new docs

Run: streamlit run src/rag/upload_dashboard.py
"""

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import requests
import streamlit as st

API_URL = os.environ.get("RAG_API_URL", "http://localhost:8000")
RAW_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

# Page config
st.set_page_config(
    page_title="RAG Pipeline - Upload & Query",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom styling
st.markdown(
    """
    <style>
    .upload-box {
        border: 2px dashed #4CAF50;
        border-radius: 8px;
        padding: 20px;
        text-align: center;
        background-color: #f0f7f0;
    }
    .success-box {
        background-color: #e8f5e9;
        padding: 15px;
        border-radius: 5px;
        border-left: 4px solid #4CAF50;
    }
    .error-box {
        background-color: #ffebee;
        padding: 15px;
        border-radius: 5px;
        border-left: 4px solid #f44336;
    }
    .info-box {
        background-color: #e3f2fd;
        padding: 15px;
        border-radius: 5px;
        border-left: 4px solid #2196F3;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Title
st.title("🚀 RAG Pipeline - Upload & Query")

# Sidebar
with st.sidebar:
    st.header("⚙️ Settings")

    mode = st.radio("Select Mode", ["Query Documents", "Upload & Manage", "Bulk Operations"])

    # Common settings
    strategy = st.selectbox(
        "Chunking Strategy",
        ["structure_aware", "fixed_overlap", "semantic"],
        index=0,
    )

    retrieval_mode = st.radio("Retrieval Mode", ["Hybrid (Dense + BM25)", "Dense Only"])
    sparse_weight = 1.0 if retrieval_mode.startswith("Hybrid") else 0.0

    top_k = st.slider("Top Results", min_value=1, max_value=8, value=4)
    use_reranker = st.checkbox("Use LLM Reranker", value=True)

    # Corpus info
    st.divider()
    st.header("📚 Corpus Status")
    try:
        response = requests.get(f"{API_URL}/v1/documents", timeout=10)
        response.raise_for_status()
        docs = response.json()
        if isinstance(docs, list):
            st.metric("Documents", len(docs))
            with st.expander("View Documents"):
                for d in docs:
                    st.caption(f"📄 {d['title']} ({d['format']})")
        else:
            st.error("Could not fetch documents")
    except requests.RequestException as e:
        st.error(f"API Error: {e}")

# ============================================================================
# MODE 1: QUERY
# ============================================================================
if mode == "Query Documents":
    st.header("📝 Query Your Documents")

    question = st.text_area(
        "Ask a question",
        placeholder="What batch size should database backfills use?",
        height=100,
    )

    col1, col2 = st.columns([1, 5])
    with col1:
        search_button = st.button("Search", type="primary")
    with col2:
        st.write("")

    if search_button and question:
        with st.spinner("🔍 Searching and generating answer..."):
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

                # Answer
                st.subheader("✨ Answer")
                st.write(data["answer"])

                # Confidence metrics
                conf = data["confidence"]
                cols = st.columns(4)
                with cols[0]:
                    st.metric("Overall", f"{conf['overall']:.2f}")
                with cols[1]:
                    st.metric("Retrieval", f"{conf['retrieval_confidence']:.2f}")
                with cols[2]:
                    st.metric("Citation", f"{conf['citation_coverage']:.2f}")
                with cols[3]:
                    st.metric("Completeness", f"{conf['completeness']:.2f}")

                # Unsupported claims
                if data["unsupported_claims"]:
                    st.warning("⚠️ Unsupported Claims:")
                    for claim in data["unsupported_claims"]:
                        st.write(
                            f"- **{claim['claim']}**: {claim['reasoning']}"
                        )

                # Sources
                st.subheader("📚 Retrieved Sources")
                for source in data["sources"]:
                    marker = "✅ Cited" if source["cited"] else "❌ Not Cited"
                    with st.expander(
                        f"[{source['index']}] {source['title']} · {marker} · "
                        f"Score: {source['score']:.2f}"
                    ):
                        st.write(source["text"])

            except requests.RequestException as e:
                st.error(f"❌ Error: {e}")

# ============================================================================
# MODE 2: UPLOAD & MANAGE
# ============================================================================
elif mode == "Upload & Manage":
    st.header("📤 Upload & Manage Documents")

    # Upload section
    st.subheader("1️⃣ Upload Documents")
    uploaded_files = st.file_uploader(
        "Drag & drop or click to upload",
        type=["md", "txt", "pdf", "html", "htm"],
        accept_multiple_files=True,
        key="file_uploader",
    )

    if uploaded_files:
        upload_status = st.container()

        with st.spinner("📤 Uploading files..."):
            temp_dir = Path(tempfile.gettempdir()) / "rag_uploads"
            temp_dir.mkdir(exist_ok=True)

            uploaded_paths = []
            for uploaded_file in uploaded_files:
                temp_path = temp_dir / uploaded_file.name
                temp_path.write_bytes(uploaded_file.getbuffer())
                uploaded_paths.append(temp_path)

            # Copy to data/raw/
            RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
            copied_files = []
            failed_files = []

            for src_path in uploaded_paths:
                try:
                    dst_path = RAW_DATA_DIR / src_path.name
                    shutil.copy2(src_path, dst_path)
                    copied_files.append(src_path.name)
                except Exception as e:
                    failed_files.append((src_path.name, str(e)))

            # Show results
            if copied_files:
                st.markdown(
                    f"""
                    <div class="success-box">
                    ✅ Successfully uploaded {len(copied_files)} file(s)
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                for filename in copied_files:
                    st.caption(f"✓ {filename}")

            if failed_files:
                st.markdown(
                    f"""
                    <div class="error-box">
                    ❌ Failed to upload {len(failed_files)} file(s)
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                for filename, error in failed_files:
                    st.caption(f"✗ {filename}: {error}")

    # Reindex section
    st.divider()
    st.subheader("2️⃣ Reindex Documents")

    col1, col2 = st.columns([1, 3])
    with col1:
        reindex_strategy = st.selectbox(
            "Strategy", ["all", "structure_aware", "fixed_overlap", "semantic"]
        )
    with col2:
        st.write("")

    if st.button("🔄 Reindex All Documents", type="secondary"):
        with st.spinner(f"Reindexing with {reindex_strategy} strategy..."):
            try:
                response = requests.post(
                    f"{API_URL}/v1/ingest",
                    json={"strategy": reindex_strategy},
                    timeout=120,
                )
                response.raise_for_status()
                data = response.json()

                st.markdown(
                    f"""
                    <div class="success-box">
                    ✅ Reindexing complete!
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                for result in data["results"]:
                    st.write(
                        f"**{result['strategy']}**: "
                        f"{result['indexed_chunks']} chunks "
                        f"({result['duplicates_dropped']} duplicates dropped)"
                    )

            except requests.RequestException as e:
                st.markdown(
                    f"""
                    <div class="error-box">
                    ❌ Reindexing failed: {e}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    # View documents
    st.divider()
    st.subheader("3️⃣ View Uploaded Documents")
    try:
        response = requests.get(f"{API_URL}/v1/documents", timeout=10)
        response.raise_for_status()
        docs = response.json()

        if not docs:
            st.info("No documents uploaded yet")
        else:
            for i, doc in enumerate(docs, 1):
                cols = st.columns([1, 3, 1, 1])
                with cols[0]:
                    st.write(f"**{i}**")
                with cols[1]:
                    st.write(f"**{doc['title']}**")
                with cols[2]:
                    st.caption(doc["format"])
                with cols[3]:
                    st.caption(doc["filename"])

    except requests.RequestException as e:
        st.error(f"Could not fetch documents: {e}")

# ============================================================================
# MODE 3: BULK OPERATIONS
# ============================================================================
else:  # Bulk Operations
    st.header("⚡ Bulk Operations")

    tab1, tab2, tab3 = st.tabs(["Upload Bulk", "Management", "Status"])

    with tab1:
        st.subheader("Bulk Upload")
        st.markdown(
            """
            Upload multiple documents at once. Supports:
            - Markdown (.md)
            - Text (.txt)
            - PDF (.pdf)
            - HTML (.html, .htm)
            """
        )

        bulk_files = st.file_uploader(
            "Upload bulk documents",
            type=["md", "txt", "pdf", "html", "htm"],
            accept_multiple_files=True,
            key="bulk_uploader",
        )

        if bulk_files:
            st.info(f"📦 {len(bulk_files)} file(s) selected")

            if st.button("📤 Upload All"):
                with st.spinner("Uploading..."):
                    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
                    success_count = 0
                    error_count = 0

                    progress_bar = st.progress(0)
                    for i, uploaded_file in enumerate(bulk_files):
                        try:
                            dst_path = RAW_DATA_DIR / uploaded_file.name
                            dst_path.write_bytes(uploaded_file.getbuffer())
                            success_count += 1
                        except Exception as e:
                            error_count += 1
                            st.error(f"Failed to upload {uploaded_file.name}: {e}")

                        progress_bar.progress((i + 1) / len(bulk_files))

                    st.success(
                        f"✅ Uploaded {success_count}/{len(bulk_files)} files"
                    )

    with tab2:
        st.subheader("Management")

        col1, col2 = st.columns(2)

        with col1:
            st.write("### Reindex Options")
            bulk_strategy = st.selectbox(
                "Strategy",
                ["all", "structure_aware", "fixed_overlap", "semantic"],
                key="bulk_strategy",
            )

            if st.button("🔄 Bulk Reindex"):
                with st.spinner("Reindexing all documents..."):
                    try:
                        response = requests.post(
                            f"{API_URL}/v1/ingest",
                            json={"strategy": bulk_strategy},
                            timeout=300,
                        )
                        response.raise_for_status()
                        data = response.json()

                        st.success("✅ Reindexing complete!")
                        for result in data["results"]:
                            with st.expander(result["strategy"]):
                                st.write(
                                    f"- Documents: {result['documents']}"
                                )
                                st.write(
                                    f"- Chunks: {result['indexed_chunks']}"
                                )
                                st.write(
                                    f"- Duplicates dropped: {result['duplicates_dropped']}"
                                )

                    except requests.RequestException as e:
                        st.error(f"Reindexing failed: {e}")

        with col2:
            st.write("### Document Management")
            if st.button("📋 List All Documents"):
                try:
                    response = requests.get(f"{API_URL}/v1/documents", timeout=10)
                    response.raise_for_status()
                    docs = response.json()

                    if docs:
                        df_data = [
                            {
                                "Filename": d["filename"],
                                "Title": d["title"],
                                "Format": d["format"],
                            }
                            for d in docs
                        ]
                        st.dataframe(df_data, use_container_width=True)
                    else:
                        st.info("No documents found")

                except requests.RequestException as e:
                    st.error(f"Could not fetch documents: {e}")

    with tab3:
        st.subheader("System Status")

        try:
            response = requests.get(f"{API_URL}/v1/documents", timeout=10)
            response.raise_for_status()
            docs = response.json()

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Documents", len(docs))
            with col2:
                st.metric("Last Updated", datetime.now().strftime("%Y-%m-%d %H:%M"))
            with col3:
                st.metric("API Status", "🟢 Online")

            st.divider()

            st.subheader("Document Breakdown")
            if docs:
                format_counts = {}
                for doc in docs:
                    fmt = doc.get("format", "unknown")
                    format_counts[fmt] = format_counts.get(fmt, 0) + 1

                cols = st.columns(len(format_counts))
                for i, (fmt, count) in enumerate(format_counts.items()):
                    with cols[i]:
                        st.metric(fmt.upper(), count)

        except requests.RequestException as e:
            st.error(f"API Error: {e}")
