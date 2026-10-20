// --- Node Types ---

export type NodeType = "file" | "symbol" | "concept" | "workflow";
export type NodeSource = "static" | "agent";

export type SymbolKind =
  | "function"
  | "class"
  | "method"
  | "const"
  | "interface"
  | "type"
  | "enum"
  | "variable"
  | "module";

export interface BaseNode {
  id: string;
  type: NodeType;
  name: string;
  source: NodeSource;
  createdAt: string;
  updatedAt: string;
}

export interface FileNode extends BaseNode {
  type: "file";
  source: "static";
  path: string;
  hash: string;
  lastIndexedAt: string;
  hotspot?: { changeCount: number; lastChanged: string };
  pagerankScore?: number;
}

export interface SymbolNode extends BaseNode {
  type: "symbol";
  source: "static";
  kind: SymbolKind;
  filePath: string;
  lineStart: number;
  lineEnd: number;
  signature: string;
  bodyHash?: string;
  signatureHash?: string;
  summary?: string;
  pagerankScore?: number;
}

export interface ConceptNode extends BaseNode {
  type: "concept";
  source: "agent";
  summary: string;
  lastUpdatedBy: string;
  stale: boolean;
  targetId?: string;
  targetHash?: string;
  status?: "hypothesis" | "verified" | "stale";
  history?: Array<{ sessionId: string; status: string; at: string }>;
}

export interface WorkflowNode extends BaseNode {
  type: "workflow";
  source: "agent";
  description: string;
  fileSequence: string[];
  lastWalkedAt: string;
  stale: boolean;
  fileHashes?: Record<string, string>;
  status?: "hypothesis" | "verified" | "stale";
  history?: Array<{ description: string; fileSequence: string[]; at: string }>;
}

export type GraphNode = FileNode | SymbolNode | ConceptNode | WorkflowNode;

// --- Edge Types ---

export type EdgeType =
  | "contains"
  | "imports"
  | "calls"
  | "extends"
  | "describes"
  | "related_to"
  | "traverses"
  | "co_changes";

export type EdgeSource = "static" | "agent";

export interface GraphEdge {
  id: string;
  type: EdgeType;
  source: EdgeSource;
  fromId: string;
  toId: string;
  data?: Record<string, unknown>;
  createdAt: string;
}

// --- Query Types ---

export interface NodeFilter {
  type?: NodeType;
  name?: string;
  /** Exact unqualified name, including the final component of a qualified name. */
  leafName?: string;
  path?: string;
  /** Exact source file or directory prefix, matched at a path boundary. */
  pathPrefix?: string;
  kind?: SymbolKind;
  stale?: boolean;
}

export interface SearchOptions {
  /** Exact source file or directory prefix, applied before candidate limits. */
  path?: string;
}

export interface SearchResult {
  node: GraphNode;
  rank: number;
  matchedField: string;
}

export interface TraversalResult {
  node: GraphNode;
  depth: number;
  path: GraphEdge[];
}
