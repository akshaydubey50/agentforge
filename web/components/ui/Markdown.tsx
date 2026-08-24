"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";

// Chat message bodies (the final answer, a knowledge_search citation, any
// free-text tool output) are markdown from the model's point of view --
// this is the one place that gets rendered as real HTML instead of shown as
// raw text, the same way ChatGPT/Claude render their own responses. Kept
// deliberately separate from raw JSON tool payloads (see StepOutput.tsx),
// which are a different shape and get formatted differently, not run
// through a markdown parser.
const components: Components = {
  p: ({ children }) => <p className="mb-2.5 leading-[1.7] text-text last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-text">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-brand underline underline-offset-2">
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="mb-2.5 ml-4.5 list-disc space-y-1 text-text last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2.5 ml-4.5 list-decimal space-y-1 text-text last:mb-0">{children}</ol>,
  li: ({ children }) => <li className="leading-[1.6] marker:text-text-faint">{children}</li>,
  h1: ({ children }) => <h1 className="mt-3 mb-1.5 text-[17px] font-semibold text-text first:mt-0">{children}</h1>,
  h2: ({ children }) => <h2 className="mt-3 mb-1.5 text-[15.5px] font-semibold text-text first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-2.5 mb-1 text-[14px] font-semibold text-text first:mt-0">{children}</h3>,
  blockquote: ({ children }) => (
    <blockquote className="mb-2.5 border-l-2 border-border-strong pl-3 text-text-muted italic last:mb-0">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-border" />,
  code: ({ className, children }) => {
    // remark maps a fenced ```block``` to <pre><code class="language-x">,
    // and inline `code` to a bare <code> with no className -- that's the
    // one reliable signal to tell them apart here.
    const isBlock = !!className;
    if (isBlock) {
      return <code className="mono block whitespace-pre-wrap">{children}</code>;
    }
    return <code className="mono rounded bg-surface-3 px-1 py-0.5 text-[0.92em] text-text">{children}</code>;
  },
  pre: ({ children }) => (
    <pre className="mb-2.5 overflow-x-auto rounded-[var(--rs)] border border-border bg-surface-2 p-3 text-[12.5px] last:mb-0">
      {children}
    </pre>
  ),
  table: ({ children }) => <Table className="mb-2.5">{children}</Table>,
  thead: ({ children }) => <TableHeader>{children}</TableHeader>,
  tbody: ({ children }) => <TableBody>{children}</TableBody>,
  tr: ({ children }) => <TableRow>{children}</TableRow>,
  th: ({ children }) => <TableHead>{children}</TableHead>,
  td: ({ children }) => <TableCell>{children}</TableCell>,
};

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("text-[14.5px]", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
