"use client";

import { FormEvent, useRef, useState } from "react";
import { CheckCircle2, FileText, Loader2, UploadCloud, XCircle } from "lucide-react";
import useSWR, { mutate } from "swr";
import { ragApi, documentRawUrl, type AskResponse, type IngestResultOut } from "@/lib/ragApi";
import { TopBar } from "@/components/shell/TopBar";
import { EmptyState } from "@/components/ui/EmptyState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

const DOCS_KEY = "rag-documents";
const CHUNKING_STRATEGY = "semantic";

type UploadStatus = "queued" | "uploading" | "indexing" | "ready" | "failed";

type UploadRow = {
  id: string;
  filename: string;
  status: UploadStatus;
  progress?: number;
  detail?: string;
  indexedChunks?: number;
  error?: string;
};

export default function KnowledgePage() {
  const {
    data: documents,
    error: docsError,
    isLoading: docsLoading,
  } = useSWR(DOCS_KEY, () => ragApi.listDocuments(), { refreshInterval: 8000 });

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadRows, setUploadRows] = useState<UploadRow[]>([]);
  const [reindexOpen, setReindexOpen] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [reindexResult, setReindexResult] = useState<IngestResultOut | null>(null);
  const [reindexError, setReindexError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [askResult, setAskResult] = useState<AskResponse | null>(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  const handleFilesPicked = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const picked = Array.from(files);
    const rows = picked.map((file) => ({
      id: `${file.name}-${file.size}-${file.lastModified}`,
      filename: file.name,
      status: "queued" as const,
      detail: "Waiting",
    }));
    setUploadRows(rows);
    setUploading(true);
    setUploadError(null);
    let firstError: string | null = null;

    const updateRow = (id: string, patch: Partial<UploadRow>) => {
      setUploadRows((current) => current.map((row) => (row.id === id ? { ...row, ...patch } : row)));
    };

    try {
      for (const [index, file] of picked.entries()) {
        const row = rows[index];
        updateRow(row.id, { status: "uploading", progress: 0, detail: "Uploading file" });
        try {
          const uploaded = await ragApi.uploadDocument(file, (progress) => {
            if (progress.phase === "indexing") {
              updateRow(row.id, { status: "indexing", progress: 100, detail: "Building semantic index" });
              return;
            }
            updateRow(row.id, {
              status: "uploading",
              progress: progress.percent,
              detail: progress.percent === undefined ? "Uploading file" : `Uploading ${progress.percent}%`,
            });
          });
          let indexResult = uploaded.index_result;
          if (!indexResult) {
            updateRow(row.id, { status: "indexing", progress: 100, detail: "Building semantic index" });
            const fallback = await ragApi.ingest(CHUNKING_STRATEGY);
            indexResult = fallback.results[0];
          }
          if (!indexResult) {
            throw new Error("Indexing finished without returning index stats.");
          }
          setReindexResult(indexResult);
          updateRow(row.id, {
            status: "ready",
            progress: 100,
            detail: "Ready for retrieval",
            indexedChunks: indexResult.indexed_chunks,
          });
        } catch (err) {
          const message = err instanceof Error ? err.message : "Upload or indexing failed";
          firstError = firstError ?? message;
          updateRow(row.id, { status: "failed", detail: "Failed", error: message });
        }
      }
      mutate(DOCS_KEY);
      if (firstError) setUploadError(firstError);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const runReindex = async () => {
    setReindexing(true);
    setReindexError(null);
    try {
      const res = await ragApi.ingest(CHUNKING_STRATEGY);
      setReindexResult(res.results[0] ?? null);
      mutate(DOCS_KEY);
      setReindexOpen(false);
    } catch (err) {
      setReindexError(err instanceof Error ? err.message : "Reindexing failed");
    } finally {
      setReindexing(false);
    }
  };

  const runQuery = async (event: FormEvent) => {
    event.preventDefault();
    const value = query.trim();
    if (!value || asking) return;
    setAsking(true);
    setAskError(null);
    try {
      setAskResult(await ragApi.ask(value));
    } catch (err) {
      setAskError(err instanceof Error ? err.message : "Retrieval query failed");
    } finally {
      setAsking(false);
    }
  };

  if (docsError) {
    return (
      <>
        <TopBar title="Knowledge" subtitle="what the assistant can retrieve" />
        <div className="flex-1 overflow-y-auto px-5 py-5">
          <EmptyState
            glyph="RAG"
            title="Cannot reach the retrieval service"
            description="The RAG API is not responding. Start the retrieval service and this page will refresh automatically."
          />
        </div>
      </>
    );
  }

  return (
    <>
      <TopBar
        title="Knowledge"
        subtitle="documents, indexing, and retrieval debugging"
        actions={
          <>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".md,.txt,.pdf,.html,.htm,.png,.jpg,.jpeg,.gif,.webp"
              className="hidden"
              onChange={(event) => handleFilesPicked(event.target.files)}
            />
            <Button size="sm" disabled={uploading} onClick={() => fileInputRef.current?.click()}>
              {uploading ? "Indexing..." : "Upload"}
            </Button>
          </>
        }
      />

      <div className="flex-1 overflow-y-auto px-5 py-5">
        <p className="mb-4 text-[12.5px] text-text-faint">
          Uploading a document saves it and automatically rebuilds the semantic retrieval index for the assistant.
        </p>

        {uploadRows.length > 0 && <UploadStatusPanel rows={uploadRows} />}

        {uploadError && (
          <Banner kind="e" title="Upload or indexing failed">
            <p>{uploadError}</p>
          </Banner>
        )}

        <h3 className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">Indexing</h3>
        <div className="mb-5 rounded-[var(--rm)] border border-border bg-surface p-4">
          <div className="flex flex-wrap items-center gap-3">
            <Dialog open={reindexOpen} onOpenChange={setReindexOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm">
                  Rebuild index
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Rebuild the search index?</DialogTitle>
                  <DialogDescription>
                    Uploads already index automatically. Use this only to recover or rebuild the semantic index from the current document folder.
                  </DialogDescription>
                </DialogHeader>
                {reindexError && (
                  <div className="px-6">
                    <Banner kind="e" title="Updating the index failed">
                      <p>{reindexError}</p>
                    </Banner>
                  </div>
                )}
                <DialogFooter>
                  <Button variant="outline" size="sm" onClick={() => setReindexOpen(false)} disabled={reindexing}>
                    Cancel
                  </Button>
                  <Button size="sm" onClick={runReindex} disabled={reindexing}>
                    {reindexing ? "Rebuilding..." : "Yes, rebuild now"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            <span className="text-[12.5px] text-text-muted">
              Upload runs chunking, embeddings, Chroma indexing, and BM25 indexing with the semantic strategy.
            </span>
          </div>

          {reindexResult && (
            <div className="mt-3 text-[12.5px] text-text">
              <b>{reindexResult.indexed_chunks}</b> chunks indexed across <b>{reindexResult.documents}</b> document
              {reindexResult.documents === 1 ? "" : "s"} ({reindexResult.duplicates_dropped} duplicate
              {reindexResult.duplicates_dropped === 1 ? "" : "s"} dropped).
            </div>
          )}
        </div>

        <h3 className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">Retrieval playground</h3>
        <section className="mb-5 rounded-[var(--rm)] border border-border bg-surface p-4">
          <form onSubmit={runQuery} className="flex gap-2 max-[760px]:flex-col">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Test a question against indexed knowledge..."
              className="min-w-0 flex-1 rounded-[var(--rs)] border border-border-strong bg-background px-3 py-2 text-[13px] text-text outline-none focus:border-role-supervisor"
            />
            <Button size="sm" disabled={!query.trim() || asking}>
              {asking ? "Retrieving..." : "Query"}
            </Button>
          </form>
          <p className="mt-2 text-[11.5px] text-text-faint">
            Calls the actual /v1/ask endpoint and displays returned sources, scores, excerpts, and confidence metadata.
          </p>
          {askError && (
            <div className="mt-3">
              <Banner kind="e" title="Retrieval failed">
                <p>{askError}</p>
              </Banner>
            </div>
          )}
          {askResult && <RetrievalResult result={askResult} />}
        </section>

        <h3 className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">Documents</h3>
        {docsLoading && <SkeletonRows />}
        {documents && documents.length === 0 && (
          <EmptyState glyph="DOC" title="No documents yet" description="Upload a file to make it retrievable." />
        )}
        {documents && documents.length > 0 && (
          <div className="tbl-scroll rounded-[var(--rm)] border border-border bg-surface">
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>Title</TableHead>
                  <TableHead>File</TableHead>
                  <TableHead>Format</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {documents.map((document) => (
                  <TableRow key={document.filename}>
                    <TableCell>
                      <div className="max-w-[280px] truncate text-text" title={document.title}>
                        {document.title}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="mono max-w-[220px] truncate text-[11.5px] text-text-muted" title={document.filename}>
                        {document.filename}
                      </div>
                    </TableCell>
                    <TableCell className="text-text-muted">{document.format}</TableCell>
                    <TableCell>
                      <a href={documentRawUrl(document.filename)} target="_blank" rel="noopener noreferrer">
                        <Button variant="outline" size="sm">
                          Open
                        </Button>
                      </a>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </>
  );
}

function UploadStatusPanel({ rows }: { rows: UploadRow[] }) {
  const active = rows.some((row) => row.status === "queued" || row.status === "uploading" || row.status === "indexing");
  const failed = rows.some((row) => row.status === "failed");
  const ready = rows.length > 0 && rows.every((row) => row.status === "ready");

  return (
    <section
      className={cn(
        "mb-4 rounded-[var(--rm)] border px-4 py-3",
        failed
          ? "border-status-failed/45 bg-status-failed/7"
          : active
            ? "border-status-running/45 bg-status-running/7"
            : "border-status-completed/35 bg-status-completed/7"
      )}
      aria-live="polite"
    >
      <div className="mb-3 flex items-center gap-2">
        {active ? (
          <Loader2 className="h-4 w-4 animate-spin text-status-running" />
        ) : failed ? (
          <XCircle className="h-4 w-4 text-status-failed" />
        ) : (
          <CheckCircle2 className="h-4 w-4 text-status-completed" />
        )}
        <div className="text-[12.5px] font-semibold text-text">
          {active ? "Processing upload" : ready ? "Upload ready" : "Upload needs attention"}
        </div>
        <div className="ml-auto text-[11px] uppercase tracking-[0.08em] text-text-faint">
          {rows.filter((row) => row.status === "ready").length}/{rows.length}
        </div>
      </div>

      <div className="space-y-2">
        {rows.map((row) => (
          <UploadStatusRow key={row.id} row={row} />
        ))}
      </div>
    </section>
  );
}

function UploadStatusRow({ row }: { row: UploadRow }) {
  const meta = UPLOAD_STATUS_META[row.status];
  const showProgress = row.status === "uploading" || row.status === "indexing";
  const width = row.status === "indexing" ? 100 : row.progress ?? 8;

  return (
    <div className="rounded-[8px] border border-border bg-rail px-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <meta.icon className={cn("h-3.5 w-3.5 flex-none", meta.iconClass, meta.spin && "animate-spin")} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-semibold text-text" title={row.filename}>
            {row.filename}
          </div>
          <div className={cn("mt-0.5 text-[11.5px]", meta.textClass)}>{row.error ?? row.detail ?? meta.label}</div>
        </div>
        <span className={cn("rounded-full px-2 py-0.5 text-[10.5px] font-bold", meta.badgeClass)}>{meta.label}</span>
      </div>
      {showProgress && (
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3">
          <div
            className={cn(
              "h-full rounded-full transition-[width] duration-300",
              row.status === "indexing" ? "animate-pulse bg-status-running" : "bg-role-human"
            )}
            style={{ width: `${Math.max(8, Math.min(100, width))}%` }}
          />
        </div>
      )}
      {row.status === "ready" && row.indexedChunks !== undefined && (
        <div className="mt-1.5 text-[11px] text-text-faint">{row.indexedChunks} chunks indexed.</div>
      )}
    </div>
  );
}

const UPLOAD_STATUS_META: Record<
  UploadStatus,
  {
    label: string;
    icon: typeof FileText;
    iconClass: string;
    textClass: string;
    badgeClass: string;
    spin?: boolean;
  }
> = {
  queued: {
    label: "Queued",
    icon: FileText,
    iconClass: "text-text-faint",
    textClass: "text-text-faint",
    badgeClass: "bg-surface-3 text-text-faint",
  },
  uploading: {
    label: "Uploading",
    icon: UploadCloud,
    iconClass: "text-role-human",
    textClass: "text-role-human",
    badgeClass: "bg-role-human/15 text-role-human",
  },
  indexing: {
    label: "Indexing",
    icon: Loader2,
    iconClass: "text-status-running",
    textClass: "text-status-running",
    badgeClass: "bg-status-running/15 text-status-running",
    spin: true,
  },
  ready: {
    label: "Ready",
    icon: CheckCircle2,
    iconClass: "text-status-completed",
    textClass: "text-status-completed",
    badgeClass: "bg-status-completed/15 text-status-completed",
  },
  failed: {
    label: "Failed",
    icon: XCircle,
    iconClass: "text-status-failed",
    textClass: "text-status-failed",
    badgeClass: "bg-status-failed/15 text-status-failed",
  },
};

function RetrievalResult({ result }: { result: AskResponse }) {
  return (
    <div className="mt-4 grid grid-cols-[minmax(260px,0.8fr)_minmax(340px,1fr)] gap-4 max-[980px]:grid-cols-1">
      <div>
        <div className="mb-2 text-[10.5px] uppercase tracking-wide text-text-faint">Answer</div>
        <div className="rounded-[8px] border border-border bg-rail px-3.5 py-3 text-[12.5px] leading-relaxed text-text-muted">
          {result.answer}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 text-[11.5px]">
          <Metric label="Retrieval" value={result.confidence.retrieval_confidence.toFixed(2)} />
          <Metric label="Coverage" value={result.confidence.citation_coverage.toFixed(2)} />
          <Metric label="Completeness" value={result.confidence.completeness.toFixed(2)} />
          <Metric label="Overall" value={result.confidence.overall.toFixed(2)} />
        </div>
        {result.unsupported_claims.length > 0 && (
          <div className="mt-3 rounded-[8px] border border-status-failed/40 bg-status-failed/8 px-3 py-2 text-[12px] text-status-failed">
            {result.unsupported_claims.length} unsupported claim{result.unsupported_claims.length === 1 ? "" : "s"} flagged by verification.
          </div>
        )}
      </div>
      <div>
        <div className="mb-2 text-[10.5px] uppercase tracking-wide text-text-faint">Retrieved chunks</div>
        <div className="space-y-2">
          {result.sources.map((source) => (
            <div key={`${source.filename}-${source.index}`} className="rounded-[8px] border border-border bg-rail px-3.5 py-3">
              <div className="mb-1 flex items-center gap-2">
                <span className="font-mono text-[11px] text-text-faint">#{source.index}</span>
                <span className="truncate text-[12px] font-semibold text-text">{source.title || source.filename}</span>
                <span className="ml-auto font-mono text-[11px] text-text-faint">{source.score.toFixed(3)}</span>
              </div>
              <div className="line-clamp-4 text-[12px] leading-relaxed text-text-muted">{source.text}</div>
              <div className="mt-2 text-[10.5px] uppercase tracking-wide text-text-faint">
                {source.cited ? "cited" : "retrieved"} - {source.filename}
              </div>
            </div>
          ))}
          {result.sources.length === 0 && <div className="rounded-[8px] border border-border bg-rail px-3.5 py-3 text-[12px] text-text-faint">No chunks returned.</div>}
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[7px] border border-border bg-rail px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.08em] text-text-faint">{label}</div>
      <div className="mt-0.5 font-mono text-text">{value}</div>
    </div>
  );
}
