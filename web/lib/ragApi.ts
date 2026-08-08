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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${RAG_API_BASE_URL}${path}`, init);
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

  uploadDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentOut>("/v1/documents/upload", { method: "POST", body: form });
  },

  ingest: (strategy: string) =>
    request<IngestResponse>("/v1/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strategy }),
    }),
};
