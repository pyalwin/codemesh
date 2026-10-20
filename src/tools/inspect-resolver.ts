/** Conservative Python call bindings. Graph call edges are not resolution proof. */
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join, posix } from "node:path";
import Parser from "tree-sitter";
import type { StorageBackend } from "../graph/storage.js";
import type { FileNode, SymbolNode } from "../graph/types.js";
import { handleSource, type SourceOutput } from "./source.js";

const require = createRequire(import.meta.url);
type Node = Parser.SyntaxNode;
export interface CallBinding {
  expression: string;
  line: number;
  resolution: "same_file_function" | "imported_module_function" | "imported_function" | "unresolved" | "ambiguous" | "unverified_graph";
  targetId?: string;
  candidateIds?: string[];
  reason?: string;
}
interface ImportBinding { module: string; imported?: string; }
interface Binding { kind: "function" | "import" | "other"; node: Node; topLevel: boolean; imported?: ImportBinding; }
interface Snapshot {
  path: string;
  tree: Parser.Tree;
  bindings: Map<string, Binding[]>;
  mutations: Set<string>;
  dynamic: boolean;
  moduleDynamic: boolean;
  hash: string;
}

function walk(node: Node, visit: (node: Node) => boolean | void): void {
  if (visit(node) === false) return;
  for (const child of node.namedChildren) walk(child, visit);
}

function patternNames(node: Node | null, output: Set<string>): void {
  if (!node) return;
  if (node.type === "identifier") { output.add(node.text); return; }
  if (node.type === "attribute" || node.type === "subscript") return;
  if (["typed_parameter", "default_parameter", "typed_default_parameter"].includes(node.type)) {
    patternNames(node.childForFieldName("name") ?? node.namedChildren[0] ?? null, output);
    return;
  }
  for (const child of node.namedChildren) patternNames(child, output);
}

function imports(node: Node): Array<{ name: string; imported: ImportBinding }> {
  const module = node.childForFieldName("module_name");
  return node.namedChildren.filter(child => child.id !== module?.id && child.type !== "wildcard_import").map(child => {
    const alias = child.childForFieldName("alias");
    const name = child.childForFieldName("name")?.text ?? child.text;
    return node.type === "import_from_statement"
      ? { name: alias?.text ?? name, imported: { module: module?.text ?? "", imported: name } }
      : { name: alias?.text ?? name.split(".")[0], imported: { module: alias ? name : name.split(".")[0] } };
  });
}

function bindingPatterns(node: Node): Node[] {
  const patterns: Array<Node | null> = [];
  if (["assignment", "augmented_assignment", "for_statement", "for_in_clause"].includes(node.type)) {
    patterns.push(node.childForFieldName("left"));
  } else if (node.type === "named_expression") {
    patterns.push(node.childForFieldName("name"));
  } else if (node.type === "as_pattern") {
    patterns.push(node.childForFieldName("alias"));
  } else if (node.type === "delete_statement") {
    patterns.push(...node.namedChildren);
  } else if (node.type === "case_pattern") {
    patterns.push(node);
  }
  return patterns.filter((pattern): pattern is Node => pattern !== null);
}

function analyzeModulePass(path: string, tree: Parser.Tree, hash: string, deadBranches = new Set<number>()): Snapshot {
  const result: Snapshot = { path, tree, hash, bindings: new Map(), mutations: new Set(), dynamic: false, moduleDynamic: false };
  const add = (name: string, binding: Binding) => result.bindings.set(name, [...(result.bindings.get(name) ?? []), binding]);
  walk(tree.rootNode, node => {
    if (deadBranches.has(node.id)) return false;
    const topLevel = node.parent?.type === "module";
    if (node.type === "function_definition" || node.type === "class_definition") {
      const name = node.childForFieldName("name");
      if (name) add(name.text, { kind: node.type === "function_definition" ? "function" : "other", node, topLevel });
      return false;
    }
    if (node.type === "import_statement" || node.type === "import_from_statement") {
      if (node.namedChildren.some(child => child.type === "wildcard_import")) result.dynamic = result.moduleDynamic = true;
      for (const item of imports(node)) add(item.name, { kind: "import", node, topLevel, imported: item.imported });
      return false;
    }
    if (node.type === "call" && ["exec", "eval", "globals", "locals"].includes(node.childForFieldName("function")?.text ?? "")) {
      result.moduleDynamic = true;
    }
    for (const pattern of bindingPatterns(node)) {
      const names = new Set<string>();
      patternNames(pattern, names);
      for (const name of names) add(name, { kind: "other", node, topLevel });
    }
  });
  // Attribute mutation and global declarations can invalidate imports/functions
  // even when they occur in another function in this file. False negatives are
  // preferable to claiming that a dynamic binding was resolved.
  walk(tree.rootNode, node => {
    if (deadBranches.has(node.id)) return false;
    if (node.type === "global_statement" || node.type === "nonlocal_statement") {
      for (const child of node.namedChildren) if (child.type === "identifier") result.mutations.add(child.text);
    }
    for (const pattern of bindingPatterns(node)) {
      walk(pattern, target => {
        if (target.type !== "attribute") return;
        const object = target.childForFieldName("object");
        if (object?.type === "identifier") result.mutations.add(object.text);
      });
    }
    if (node.type === "call") {
      const fn = node.childForFieldName("function");
      if (["exec", "eval", "globals", "locals"].includes(fn?.text ?? "")) result.dynamic = true;
      if (["setattr", "delattr"].includes(fn?.text ?? "")) {
        const object = node.childForFieldName("arguments")?.namedChildren[0];
        if (object?.type === "identifier") result.mutations.add(object.text);
      }
    }
  });
  return result;
}

function analyzeModule(path: string, tree: Parser.Tree, hash: string, allowTypingGuards: boolean): Snapshot {
  const initial = analyzeModulePass(path, tree, hash);
  if (!allowTypingGuards) return initial;
  const constants = new Set<string>();
  const modules = new Set<string>();
  for (const [name, bindings] of initial.bindings) {
    if (bindings.length !== 1 || initial.mutations.has(name)) continue;
    const binding = bindings[0];
    if (binding.kind !== "import" || !binding.topLevel || binding.imported?.module !== "typing") continue;
    if (binding.imported.imported === "TYPE_CHECKING") constants.add(name);
    else if (binding.imported.imported === undefined) modules.add(name);
  }
  if (!constants.size && !modules.size) return initial;
  const deadBranches = new Set<number>();
  walk(tree.rootNode, node => {
    // Only module execution scope is eligible. A function/class can shadow
    // the imported constant in its own scope even when the module cannot.
    if (node.type === "function_definition" || node.type === "class_definition") return false;
    if (node.type !== "if_statement" && node.type !== "elif_clause") return;
    const condition = node.childForFieldName("condition");
    const typingConstant = condition?.type === "identifier" && constants.has(condition.text);
    const typingAttribute = condition?.type === "attribute"
      && condition.childForFieldName("attribute")?.text === "TYPE_CHECKING"
      && modules.has(condition.childForFieldName("object")?.text ?? "");
    const consequence = node.childForFieldName("consequence");
    if ((typingConstant || typingAttribute) && consequence) deadBranches.add(consequence.id);
  });
  return deadBranches.size ? analyzeModulePass(path, tree, hash, deadBranches) : initial;
}

function locals(fn: Node): Set<string> {
  const names = new Set<string>();
  patternNames(fn.childForFieldName("parameters"), names);
  const body = fn.childForFieldName("body");
  if (body) walk(body, node => {
    if (["function_definition", "class_definition"].includes(node.type)) {
      patternNames(node.childForFieldName("name"), names);
      return false;
    }
    if (node.type === "lambda") return false;
    if (node.type === "import_statement" || node.type === "import_from_statement") {
      for (const item of imports(node)) names.add(item.name);
      return false;
    }
    for (const pattern of bindingPatterns(node)) patternNames(pattern, names);
  });
  return names;
}

function modulePath(from: string, module: string): string | null {
  const dots = module.match(/^\.+/)?.[0].length ?? 0;
  const tail = module.slice(dots).split(".").filter(Boolean);
  if (tail.some(part => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(part))) return null;
  const result = dots ? posix.normalize(posix.join(posix.dirname(from), ...Array(dots - 1).fill(".."), ...tail)) : tail.join("/");
  return result.startsWith("../") || result === ".." || posix.isAbsolute(result) ? null : result;
}

export async function resolveInspectCalls(storage: StorageBackend, root: SourceOutput, projectRoot: string): Promise<{
  calls: CallBinding[]; snapshots: Array<{ path: string; hash: string }>;
}> {
  const cache = new Map<string, Snapshot | null>();
  const snapshot = async (path: string): Promise<Snapshot | null> => {
    if (cache.has(path)) return cache.get(path)!;
    const file = await storage.getNode(`file:${path}`) as FileNode | null;
    try {
      const bytes = readFileSync(join(projectRoot, path));
      if (file?.type !== "file" || createHash("sha256").update(bytes).digest("hex") !== file.hash) {
        cache.set(path, null); return null;
      }
      const parser = new Parser();
      parser.setLanguage(require("tree-sitter-python") as Parameters<Parser["setLanguage"]>[0]);
      const tree = parser.parse(bytes.toString("utf-8"));
      if (tree.rootNode.hasError) { cache.set(path, null); return null; }
      // A project-local typing module is not the standard library constant.
      const standardTyping = !existsSync(join(projectRoot, "typing.py"))
        && !existsSync(join(projectRoot, "typing", "__init__.py"));
      const parsed = analyzeModule(path, tree, file.hash, standardTyping);
      cache.set(path, parsed);
      return parsed;
    } catch { cache.set(path, null); return null; }
  };
  const findModule = async (stem: string): Promise<string | null> => {
    const paths = [`${stem}.py`, `${stem}/__init__.py`];
    const found = [];
    for (const path of paths) if ((await storage.getNode(`file:${path}`))?.type === "file") found.push(path);
    return found.length === 1 ? found[0] : null;
  };
  const packageChainSafe = async (filePath: string): Promise<boolean> => {
    const parts = filePath.split("/");
    for (let index = 1; index < parts.length; index++) {
      const packagePath = `${parts.slice(0, index).join("/")}/__init__.py`;
      if (packagePath === filePath) continue;
      if (!(await storage.getNode(`file:${packagePath}`))) {
        if (existsSync(join(projectRoot, packagePath))) return false;
        continue; // A genuinely absent initializer is a namespace package.
      }
      const parent = await snapshot(packagePath);
      const child = parts[index].replace(/\.py$/, "");
      // Dotted imports execute ancestor initializers. Attribute writes there
      // can replace a child's function before the caller binds its import.
      // A lazy __getattr__ body alone does not run when importing an existing
      // submodule; dynamic statements at module execution scope do.
      if (!parent || parent.moduleDynamic || parent.mutations.has(child)
          || (parent.bindings.get(child) ?? []).some(binding => binding.kind !== "import")) return false;
    }
    return true;
  };
  const target = async (filePath: string, name: string): Promise<Partial<CallBinding>> => {
    const candidates = (await storage.queryNodes({ type: "symbol", name })).filter((node): node is SymbolNode =>
      node.type === "symbol" && node.filePath === filePath && node.kind === "function");
    if (candidates.length > 1) return { resolution: "ambiguous", candidateIds: candidates.map(node => node.id).sort(), reason: "multiple_function_declarations" };
    const file = await snapshot(filePath);
    if (!file) return { resolution: "unresolved", reason: "callee_file_stale_or_unavailable" };
    if (!await packageChainSafe(filePath)) return { resolution: "unresolved", reason: "ancestor_package_binding_not_proven" };
    const bindings = file.bindings.get(name) ?? [];
    if (file.dynamic || file.mutations.has(name) || bindings.length !== 1 || bindings[0].kind !== "function" || !bindings[0].topLevel) {
      return { resolution: "unresolved", reason: "callee_binding_rebound_decorated_or_conditional" };
    }
    const candidate = candidates[0];
    if (!candidate || candidate.lineStart !== bindings[0].node.startPosition.row + 1
        || candidate.lineEnd !== bindings[0].node.endPosition.row + 1) {
      return { resolution: "unresolved", reason: "callee_has_no_exact_indexed_span" };
    }
    return { targetId: candidate.id };
  };

  if (!root.filePath.endsWith(".py")) {
    const edges = await storage.getEdges(root.id, "out", ["calls"]);
    return { calls: edges.map(edge => ({ expression: edge.toId, line: root.lineStart,
      resolution: "unverified_graph", candidateIds: [edge.toId], reason: "language_binding_resolution_unavailable" })), snapshots: [] };
  }
  const file = await snapshot(root.filePath);
  if (!file) return { calls: [{ expression: root.symbol, line: root.lineStart, resolution: "unresolved", reason: "caller_file_stale_or_parse_unavailable" }], snapshots: [] };
  let fn: Node | undefined;
  walk(file.tree.rootNode, node => {
    if (node.type === "function_definition" && node.startPosition.row + 1 === root.lineStart
        && node.endPosition.row + 1 === root.lineEnd) fn = node;
  });
  if (!fn) return { calls: [{ expression: root.symbol, line: root.lineStart, resolution: "unresolved", reason: "only_function_bodies_have_direct_call_resolution" }], snapshots: [{ path: file.path, hash: file.hash }] };
  const shadowed = locals(fn);
  let enclosingFunction = false;
  for (let parent = fn.parent; parent; parent = parent.parent) if (parent.type === "function_definition") enclosingFunction = true;
  const calls: Node[] = [];
  const body = fn.childForFieldName("body");
  if (body) walk(body, node => {
    if (["function_definition", "class_definition", "lambda"].includes(node.type)) return false;
    if (node.type === "call") calls.push(node);
  });
  const resolved: CallBinding[] = [];
  for (const call of calls) {
    const expression = call.childForFieldName("function");
    const base: CallBinding = { expression: expression?.text ?? "<dynamic>", line: call.startPosition.row + 1, resolution: "unresolved" };
    if (!expression || file.dynamic || enclosingFunction) { resolved.push({ ...base, reason: "dynamic_or_closure_scope" }); continue; }
    const receiver = expression.type === "identifier" ? expression : expression.type === "attribute" ? expression.childForFieldName("object") : null;
    if (receiver?.type !== "identifier") { resolved.push({ ...base, reason: "dynamic_receiver" }); continue; }
    const name = receiver.text;
    if (shadowed.has(name) || file.mutations.has(name)) { resolved.push({ ...base, reason: "shadowed_or_mutated_binding" }); continue; }
    const bindings = file.bindings.get(name) ?? [];
    if (expression.type === "identifier" && bindings.every(binding => binding.kind === "function") && bindings.length) {
      resolved.push({ ...base, resolution: "same_file_function", ...await target(root.filePath, name) });
      continue;
    }
    if (bindings.length !== 1 || bindings[0].kind !== "import" || !bindings[0].topLevel || !bindings[0].imported) {
      resolved.push({ ...base, reason: bindings.length ? "rebound_or_non_import_receiver" : "unbound_or_external_name" }); continue;
    }
    const imported = bindings[0].imported;
    const stem = modulePath(root.filePath, imported.module);
    if (stem === null) { resolved.push({ ...base, reason: "import_outside_project" }); continue; }
    if (expression.type === "identifier") {
      const path = imported.imported ? await findModule(stem) : null;
      if (!path || !imported.imported) { resolved.push({ ...base, reason: "external_or_dynamic_import" }); continue; }
      resolved.push({ ...base, resolution: "imported_function", ...await target(path, imported.imported) });
    } else {
      const member = expression.childForFieldName("attribute")?.text;
      let path: string | null;
      if (imported.imported) {
        const packagePath = await findModule(stem);
        const parent = packagePath?.endsWith("/__init__.py") ? await snapshot(packagePath) : null;
        if (!parent || parent.dynamic || parent.bindings.has(imported.imported)
            || parent.mutations.has(imported.imported) || parent.bindings.has("__getattr__")) {
          resolved.push({ ...base, reason: "package_attribute_binding_not_proven" }); continue;
        }
        path = await findModule(posix.join(stem, imported.imported));
      } else path = await findModule(stem);
      if (!path || !member) { resolved.push({ ...base, reason: "external_or_dynamic_module" }); continue; }
      resolved.push({ ...base, resolution: "imported_module_function", ...await target(path, member) });
    }
  }
  return { calls: resolved, snapshots: [...cache.values()].filter((item): item is Snapshot => item !== null)
    .map(item => ({ path: item.path, hash: item.hash })) };
}

/** Incoming discovery is a bounded graph hint set, never an exhaustive call graph. */
export interface IncomingCoverage {
  discovery: "indexed_call_edges";
  complete: false;
  candidateCount: number;
  checked: number;
  verified: number;
  unverified: number;
  omitted: number;
  reason: "candidate_limit" | "graph_may_miss_callers";
}
export interface IncomingCallBinding extends CallBinding {
  sourceId: string;
  failureStatus?: "stale" | "deleted";
}

/**
 * Graph edges select candidates only. Re-resolve the candidate's actual current
 * Python calls and accept only exact target IDs. This deliberately does not
 * scan the repository: aliases missing from the indexed graph remain a stated
 * coverage limitation until a call-site/import-reference index is available.
 */
export async function resolveInspectIncoming(
  storage: StorageBackend, root: SourceOutput, projectRoot: string, maxCandidates: number,
): Promise<{
  calls: IncomingCallBinding[];
  sources: SourceOutput[];
  snapshots: Array<{ path: string; hash: string }>;
  coverage: IncomingCoverage;
}> {
  const ids = [...new Set((await storage.getEdges(root.id, "in", ["calls"]))
    .filter(edge => edge.toId === root.id).map(edge => edge.fromId))].sort();
  const coverage: IncomingCoverage = {
    discovery: "indexed_call_edges", complete: false, candidateCount: ids.length,
    checked: 0, verified: 0, unverified: 0, omitted: Math.max(0, ids.length - maxCandidates),
    reason: ids.length > maxCandidates ? "candidate_limit" : "graph_may_miss_callers",
  };
  const result: Awaited<ReturnType<typeof resolveInspectIncoming>> = {
    calls: [], sources: [], snapshots: [], coverage,
  };
  for (const id of ids.slice(0, maxCandidates)) {
    coverage.checked++;
    const node = await storage.getNode(id);
    const unverified = (reason: string, failureStatus?: "stale" | "deleted"): void => {
      coverage.unverified++;
      result.calls.push({ sourceId: id, expression: root.symbol,
        line: node?.type === "symbol" ? node.lineStart : 0,
        resolution: "unverified_graph", candidateIds: [root.id], reason,
        ...(failureStatus ? { failureStatus } : {}),
      });
    };
    if (node?.type !== "symbol") { unverified("caller_symbol_unavailable"); continue; }
    if (!node.filePath.endsWith(".py")) { unverified("language_binding_resolution_unavailable"); continue; }
    const source = await handleSource(storage, { id, mode: "full", force: true }, projectRoot);
    if (source.status !== "fresh") {
      unverified("caller_source_not_current", source.status === "deleted" ? "deleted" : "stale");
      continue;
    }
    const resolved = await resolveInspectCalls(storage, source, projectRoot);
    // Record every read used to prove the binding, including import packages.
    result.snapshots.push(...resolved.snapshots);
    const matches = resolved.calls.filter(call => call.targetId === root.id);
    if (!matches.length) { unverified("no_verified_call_to_target"); continue; }
    coverage.verified++;
    result.sources.push(source);
    result.calls.push(...matches.map(call => ({ ...call, sourceId: id })));
  }
  return result;
}
