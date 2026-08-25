"use client";

import { FormEvent, useRef, useState } from "react";
import useSWR, { mutate } from "swr";
import { ragApi, documentRawUrl, type AskResponse, type IngestResultOut } from "@/lib/ragApi";
import { TopBar } from "@/components/shell/TopBar";
import { EmptyState } from "@/components/ui/EmptyState";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
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

export default function KnowledgePage() {
  const {
    data: documents,
    error: docsError,
    isLoading: docsLoading,
  } = useSWR(DOCS_KEY, () => ragApi.listDocuments(), { refreshInterval: 8000 });

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
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
    setUploading(true);
    setUploadError(null);
    try {
      for (const file of Array.from(files)) {
        await ragApi.uploadDocument(file);
      }
      mutate(DOCS_KEY);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
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
              {uploading ? "Uploading..." : "Upload"}
            </Button>
          </>
        }
      />

      <div className="flex-1 overflow-y-auto px-5 py-5">
        <p className="mb-4 text-[12.5px] text-text-faint">
          The assistant can retrieve these indexed sources through the RAG service. Documents only become searchable after the index is updated.
        </p>

        {uploadError && (
          <Banner kind="e" title="Upload failed">
            <p>{uploadError}</p>
          </Banner>
        )}

        <h3 className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">Indexing</h3>
        <div className="mb-5 rounded-[var(--rm)] border border-border bg-surface p-4">
          <div className="flex flex-wrap items-center gap-3">
            <Dialog open={reindexOpen} onOpenChange={setReindexOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm">
                  Update index
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Update the search index?</DialogTitle>
                  <DialogDescription>
                    This calls the real embedding pipeline and rebuilds the index. It can take a while and may use external API credits for a large corpus.
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
                    {reindexing ? "Updating..." : "Yes, update now"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            <span className="text-[12.5px] text-text-muted">
              Uses the backend semantic chunking strategy; no unsupported reranking controls are exposed here.
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
          <EmptyState glyph="DOC" title="No documents yet" description="Upload a file and update the index to make it retrievable." />
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
