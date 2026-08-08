// Non-technical users should never see "db_query" -- this is the translation
// layer between the raw tool names/descriptions GET /v1/tools returns and
// what the Tools module actually shows. Known first-party tools get a
// hand-written friendly entry; anything else (including every MCP-discovered
// tool, which is already namespaced mcp_<server>_<tool>) falls back to a
// prettified name plus its own description -- MCPTool already writes those
// in plain English (see src/agentsys/tools/mcp_tool.py), so the fallback
// stays readable for tools this catalog has never seen.

export interface CatalogEntry {
  label: string;
  icon: string;
  friendlyDescription: string;
}

const KNOWN: Record<string, CatalogEntry> = {
  db_query: {
    label: "Your database",
    icon: "📊",
    friendlyDescription: "Reads your metrics. Read-only — it can never change your data.",
  },
  web_search: {
    label: "Web search",
    icon: "🌐",
    friendlyDescription: "Looks things up on the web.",
  },
  code_execution: {
    label: "Calculations",
    icon: "🧮",
    friendlyDescription: "Runs real maths in a sealed sandbox instead of guessing.",
  },
  file_io: {
    label: "Files",
    icon: "📄",
    friendlyDescription: "Writes documents and reads them back.",
  },
  delegate_subagent: {
    label: "Helper agent",
    icon: "🧩",
    friendlyDescription: "Hands off part of the work to a focused sub-agent.",
  },
  knowledge_search: {
    label: "Your documents",
    icon: "◫",
    friendlyDescription: "Reads what's in Knowledge and answers with real citations.",
  },
};

function prettifyName(name: string): string {
  const withoutMcpPrefix = name.startsWith("mcp_") ? name.slice(4) : name;
  return withoutMcpPrefix
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function catalogEntry(name: string, description: string): CatalogEntry {
  if (KNOWN[name]) return KNOWN[name];

  if (name.startsWith("mcp_")) {
    // name shape: mcp_<server>_<tool> -- best-effort split, server name is
    // whatever comes before the last recognizable tool segment isn't knowable
    // without the registry's own bookkeeping, so just show the whole thing
    // prettified plus its real description.
    return {
      label: prettifyName(name),
      icon: "🔌",
      friendlyDescription: description || "A custom tool connected via MCP.",
    };
  }

  return {
    label: prettifyName(name),
    icon: "🔧",
    friendlyDescription: description,
  };
}
