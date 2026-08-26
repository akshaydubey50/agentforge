// Client for src/rag's separate FastAPI service (its own port, its own
// process -- agentsys and rag don't import each other, see docs/MERGE.md).
// Hand-mirrored from src/rag/schemas.py, same "kept in sync by hand" call
// as lib/api.ts.

export const RAG_API_BASE_URL =
  process.env.NEXT_PUBLIC_RAG_API_BASE_URL || "http://localhost:8000";

export interface DocumentOut {
  filename: string;
  format: string;
  title: string;
}

export interface IngestResultOut {
  strategy: string;
  documents: number;
  input_chunks: number;
  indexed_chunks: number;
  duplicates_dropped: number;
}

export interface IngestResponse {
  results: IngestResultOut[];
}

export interface UploadDocumentResponse {
  document: DocumentOut;
  index_result?: IngestResultOut;
}

export type UploadProgress =
  | { phase: "uploading"; loaded: number; total?: number; percent?: number }
  | { phase: "indexing" };

export interface AskSourceOut {
  index: number;
  filename: string;
  title: string;
  text: string;
  score: number;
  cited: boolean;
}

export interface AskResponse {
  question: string;
  answer: string;
  cited_indices: number[];
  sources: AskSourceOut[];
  confidence: {
    retrieval_confidence: number;
    citation_coverage: number;
    completeness: number;
    overall: number;
  };
  unsupported_claims: { claim: string; cited_source_numbers: number[]; reasoning: string }[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${RAG_API_BASE_URL}${path}`, { ...init, credentials: "include" });
  if (res.status === 401) {
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new Error("not authenticated");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${init?.method || "GET"} ${path} failed: ${res.status} ${body}`);
  }
  return res.json() as Promise<T>;
}

export function documentRawUrl(filename: string): string {
  return `${RAG_API_BASE_URL}/v1/documents/${encodeURIComponent(filename)}/raw`;
}

export const ragApi = {
  listStrategies: () =>
    request<{ strategies: string[] }>("/v1/strategies").then((r) => r.strategies),

  listDocuments: () => request<DocumentOut[]>("/v1/documents"),

  uploadDocument: (file: File, onProgress?: (progress: UploadProgress) => void) => {
    const form = new FormData();
    form.append("file", file);
    if (typeof XMLHttpRequest === "undefined") {
      return request<UploadDocumentResponse>("/v1/documents/upload", { method: "POST", body: form });
    }

    return new Promise<UploadDocumentResponse>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${RAG_API_BASE_URL}/v1/documents/upload`);
      xhr.withCredentials = true;

      xhr.upload.onprogress = (event) => {
        const total = event.lengthComputable ? event.total : undefined;
        onProgress?.({
          phase: "uploading",
          loaded: event.loaded,
          total,
          percent: total ? Math.round((event.loaded / total) * 100) : undefined,
        });
      };
      xhr.upload.onload = () => onProgress?.({ phase: "indexing" });
      xhr.onerror = () => reject(new Error("POST /v1/documents/upload failed: network error"));
      xhr.onload = () => {
        if (xhr.status === 401) {
          if (typeof window !== "undefined") window.location.href = "/login";
          reject(new Error("not authenticated"));
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(`POST /v1/documents/upload failed: ${xhr.status} ${xhr.responseText}`));
          return;
        }
        try {
          resolve(JSON.parse(xhr.responseText) as UploadDocumentResponse);
        } catch (err) {
          reject(err);
        }
      };
      xhr.send(form);
    });
  },

  ingest: (strategy: string) =>
    request<IngestResponse>("/v1/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strategy }),
    }),

  ask: (question: string) =>
    request<AskResponse>("/v1/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, strategy: "semantic", top_k: 5, use_reranker: true, sparse_weight: 1.0 }),
    }),
};
