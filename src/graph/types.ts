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
}

export interface SymbolNode extends BaseNode {
  type: "symbol";
  source: "static";
  kind: SymbolKind;
  filePath: string;
  lineStart: number;
  lineEnd: number;
  signature: string;
}

export interface ConceptNode extends BaseNode {
  type: "concept";
  source: "agent";
  summary: string;
  lastUpdatedBy: string;
  stale: boolean;
}

export interface WorkflowNode extends BaseNode {
  type: "workflow";
  source: "agent";
  description: string;
  fileSequence: string[];
  lastWalkedAt: string;
  stale: boolean;
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
  path?: string;
  kind?: SymbolKind;
  stale?: boolean;
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
