"use client";

import { useRef, useState } from "react";
import useSWR, { mutate } from "swr";
import { ragApi, documentRawUrl, type IngestResultOut } from "@/lib/ragApi";
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

// Only strategy exposed to the product -- "which chunking strategy" is a RAG
// research knob (see the golden-set eval in docs/RAG_PIPELINE.md), not a
// decision a user of the product should ever have to make. semantic is kept
// over structure_aware because it decides chunk boundaries from meaning, not
// markdown header syntax -- the corpus mixes PDFs/HTML/txt with no headers,
// where structure_aware silently degrades to naive fixed-size chunking.
const CHUNKING_STRATEGY = "semantic";

export default function KnowledgePage() {
  const { data: documents, error: docsError, isLoading: docsLoading } = useSWR(
    DOCS_KEY,
    () => ragApi.listDocuments(),
    { refreshInterval: 8000 }
  );

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [reindexOpen, setReindexOpen] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [reindexResult, setReindexResult] = useState<IngestResultOut | null>(null);
  const [reindexError, setReindexError] = useState<string | null>(null);

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

  if (docsError) {
    return (
      <>
        <TopBar title="Knowledge" subtitle="what the assistant can read" />
        <div className="flex-1 overflow-y-auto px-5 py-5">
          <EmptyState
            glyph="🔌"
            title="Can't reach the retrieval service"
            description="The rag-api service isn't responding. Run `docker compose up -d rag-api` (or start it locally) and this page will pick it up automatically."
          />
        </div>
      </>
    );
  }

  return (
    <>
      <TopBar
        title="Knowledge"
        subtitle="what the assistant can read"
        actions={
          <>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".md,.txt,.pdf,.html,.htm,.png,.jpg,.jpeg,.gif,.webp"
              className="hidden"
              onChange={(e) => handleFilesPicked(e.target.files)}
            />
            <Button size="sm" disabled={uploading} onClick={() => fileInputRef.current?.click()}>
              {uploading ? "Uploading…" : "Upload"}
            </Button>
          </>
        }
      />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <p className="mb-4 text-[12.5px] text-text-faint">
          The assistant can read these — ask about them directly in{" "}
          <a href="/ask" className="text-brand underline">
            Ask
          </a>
          . This page manages the corpus; documents only become searchable after you{" "}
          <b>Update index</b> below.
        </p>

        {uploadError && (
          <Banner kind="e" title="Upload failed">
            <p>{uploadError}</p>
          </Banner>
        )}

        <h3 className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">
          Make new uploads searchable
        </h3>
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
                    This calls the real OpenAI embedding API and rebuilds the whole index, not
                    just new files — it can take a while and costs real credits for a large
                    corpus.
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
                    {reindexing ? "Updating…" : "Yes, update now"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            <span className="text-[12.5px] text-text-muted">
              Rebuilds the whole index, not just new files — takes longer as the corpus grows.
            </span>
          </div>

          {reindexResult && (
            <div className="mt-3 text-[12.5px] text-text">
              <b>{reindexResult.indexed_chunks}</b> chunks indexed across{" "}
              <b>{reindexResult.documents}</b> document{reindexResult.documents === 1 ? "" : "s"} (
              {reindexResult.duplicates_dropped} duplicate{reindexResult.duplicates_dropped === 1 ? "" : "s"} dropped)
            </div>
          )}
        </div>

        <h3 className="mb-2.5 text-[11px] font-extrabold uppercase tracking-wide text-text-faint">Documents</h3>
        {docsLoading && <SkeletonRows />}
        {documents && documents.length === 0 && (
          <EmptyState glyph="◫" title="No documents yet" description="Upload a file and it'll show up here, ready to index." />
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
                {documents.map((d) => (
                  <TableRow key={d.filename}>
                    <TableCell>
                      <div className="max-w-[280px] truncate text-text" title={d.title}>
                        {d.title}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="mono max-w-[220px] truncate text-[11.5px] text-text-muted" title={d.filename}>
                        {d.filename}
                      </div>
                    </TableCell>
                    <TableCell className="text-text-muted">{d.format}</TableCell>
                    <TableCell>
                      <a href={documentRawUrl(d.filename)} target="_blank" rel="noopener noreferrer">
                        <Button variant="outline" size="sm">
                          Open ↗
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
