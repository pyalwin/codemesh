/**
 * Tree-sitter based parser that extracts symbols, imports, and call
 * relationships from source files.
 */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import Parser from "tree-sitter";
import { getLanguageConfig } from "./languages.js";
const require = createRequire(import.meta.url);
/** Grammar objects keyed by language name, loaded lazily */
const grammarCache = new Map();
/** Reusable parser instance */
let parserInstance = null;
function getParser() {
    if (!parserInstance) {
        parserInstance = new Parser();
    }
    return parserInstance;
}
/**
 * Dynamically load a tree-sitter grammar by package name and language name.
 * Results are cached so each grammar is loaded once.
 */
function loadGrammar(grammarPackage, languageName) {
    const cached = grammarCache.get(languageName);
    if (cached)
        return cached;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require(grammarPackage);
    // tree-sitter-typescript exports { typescript, tsx }
    // most others export the grammar directly as default
    let grammar;
    if (languageName === "typescript") {
        grammar = mod.typescript;
    }
    else if (languageName === "tsx") {
        grammar = mod.tsx;
    }
    else {
        grammar = mod;
    }
    grammarCache.set(languageName, grammar);
    return grammar;
}
// ─── Entry point ────────────────────────────────────────────────────
const EMPTY_RESULT = { symbols: [], imports: [], calls: [] };
/**
 * Parse a source file and extract symbols, imports, and call references.
 *
 * @param absolutePath - Full filesystem path (used to read the file)
 * @param relativePath - Project-relative path (currently unused but reserved for future use)
 * @returns ParseResult with extracted data, or empty result for unsupported files
 */
export async function parseFile(absolutePath, _relativePath) {
    const config = getLanguageConfig(absolutePath);
    if (!config)
        return EMPTY_RESULT;
    let grammar;
    try {
        grammar = loadGrammar(config.grammarPackage, config.name);
    }
    catch {
        // Grammar not available (e.g. native compilation failed)
        return EMPTY_RESULT;
    }
    const source = readFileSync(absolutePath, "utf-8");
    const parser = getParser();
    parser.setLanguage(grammar);
    const tree = parser.parse(source);
    const symbols = [];
    const imports = [];
    const calls = [];
    if (config.name === "typescript" || config.name === "javascript") {
        walkTS(tree.rootNode, symbols, imports, calls, source);
    }
    else if (config.name === "python") {
        walkPython(tree.rootNode, symbols, imports, calls, source);
    }
    else if (config.queryFile) {
        const { parseWithQuery } = await import("./query-parser.js");
        const res = parseWithQuery(tree, grammar, config.name, config.queryFile, source);
        symbols.push(...res.symbols);
        imports.push(...res.imports);
        calls.push(...res.calls);
    }
    else {
        // For languages without a dedicated walker, do a generic walk
        walkGeneric(tree.rootNode, symbols, imports, calls, source);
    }
    return { symbols, imports, calls };
}
// ─── TypeScript / JavaScript walker ─────────────────────────────────
function walkTS(node, symbols, imports, calls, _source) {
    walkTSNode(node, symbols, imports, calls, []);
}
function walkTSNode(node, symbols, imports, calls, scopeStack) {
    switch (node.type) {
        // ── Exports wrap the real declaration ──
        case "export_statement": {
            // Look for the declaration child (function_declaration, class_declaration, etc.)
            for (const child of node.children) {
                if (child.type === "function_declaration" ||
                    child.type === "class_declaration" ||
                    child.type === "lexical_declaration" ||
                    child.type === "interface_declaration" ||
                    child.type === "type_alias_declaration" ||
                    child.type === "enum_declaration") {
                    walkTSNode(child, symbols, imports, calls, scopeStack);
                }
            }
            return; // Don't recurse into children again
        }
        // ── Function declarations ──
        case "function_declaration": {
            const nameNode = node.childForFieldName("name");
            if (nameNode) {
                symbols.push({
                    name: nameNode.text,
                    kind: "function",
                    lineStart: node.startPosition.row + 1,
                    lineEnd: node.endPosition.row + 1,
                    signature: extractTSFunctionSignature(node),
                    scopePath: [...scopeStack],
                });
            }
            // Walk body for calls
            walkTSChildren(node, symbols, imports, calls, scopeStack);
            return;
        }
        // ── Class declarations ──
        case "class_declaration": {
            const nameNode = node.childForFieldName("name");
            if (nameNode) {
                const className = nameNode.text;
                symbols.push({
                    name: className,
                    kind: "class",
                    lineStart: node.startPosition.row + 1,
                    lineEnd: node.endPosition.row + 1,
                    signature: `class ${className}`,
                    scopePath: [...scopeStack],
                });
                // Walk body with class pushed onto scope stack
                const body = node.childForFieldName("body");
                if (body) {
                    walkTSChildren(body, symbols, imports, calls, [
                        ...scopeStack,
                        className,
                    ]);
                }
            }
            return;
        }
        // ── Methods inside class body ──
        case "method_definition": {
            const nameNode = node.childForFieldName("name");
            if (nameNode) {
                const methodName = nameNode.text;
                symbols.push({
                    // Emit the bare name; scopePath carries enclosing class(es).
                    name: methodName,
                    kind: "method",
                    lineStart: node.startPosition.row + 1,
                    lineEnd: node.endPosition.row + 1,
                    signature: extractTSMethodSignature(node, scopeStack[scopeStack.length - 1] ?? null),
                    scopePath: [...scopeStack],
                });
            }
            walkTSChildren(node, symbols, imports, calls, scopeStack);
            return;
        }
        // ── Interface declarations ──
        case "interface_declaration": {
            const nameNode = node.childForFieldName("name");
            if (nameNode) {
                symbols.push({
                    name: nameNode.text,
                    kind: "interface",
                    lineStart: node.startPosition.row + 1,
                    lineEnd: node.endPosition.row + 1,
                    signature: `interface ${nameNode.text}`,
                    scopePath: [...scopeStack],
                });
            }
            return;
        }
        // ── Type alias declarations ──
        case "type_alias_declaration": {
            const nameNode = node.childForFieldName("name");
            if (nameNode) {
                symbols.push({
                    name: nameNode.text,
                    kind: "type",
                    lineStart: node.startPosition.row + 1,
                    lineEnd: node.endPosition.row + 1,
                    signature: `type ${nameNode.text}`,
                    scopePath: [...scopeStack],
                });
            }
            return;
        }
        // ── Enum declarations ──
        case "enum_declaration": {
            const nameNode = node.childForFieldName("name");
            if (nameNode) {
                symbols.push({
                    name: nameNode.text,
                    kind: "enum",
                    lineStart: node.startPosition.row + 1,
                    lineEnd: node.endPosition.row + 1,
                    signature: `enum ${nameNode.text}`,
                    scopePath: [...scopeStack],
                });
            }
            return;
        }
        // ── Lexical declarations (const, let) ──
        case "lexical_declaration": {
            // Only track top-level const declarations
            for (const child of node.children) {
                if (child.type === "variable_declarator") {
                    const nameNode = child.childForFieldName("name");
                    if (nameNode) {
                        symbols.push({
                            name: nameNode.text,
                            kind: "const",
                            lineStart: node.startPosition.row + 1,
                            lineEnd: node.endPosition.row + 1,
                            signature: `const ${nameNode.text}`,
                            scopePath: [...scopeStack],
                        });
                    }
                }
            }
            // Walk value expressions for calls
            walkTSChildren(node, symbols, imports, calls, scopeStack);
            return;
        }
        // ── Import statements ──
        case "import_statement": {
            const sourceNode = node.childForFieldName("source");
            if (sourceNode) {
                // Extract the string value without quotes
                const raw = sourceNode.text;
                const importPath = raw.replace(/^['"]|['"]$/g, "");
                imports.push(importPath);
            }
            return;
        }
        // ── Call expressions ──
        case "call_expression": {
            const fnNode = node.childForFieldName("function");
            if (fnNode) {
                const callee = extractCalleeName(fnNode);
                if (callee) {
                    calls.push({
                        callee,
                        lineNumber: node.startPosition.row + 1,
                        scopePath: [...scopeStack],
                    });
                }
            }
            // Also walk arguments for nested calls
            walkTSChildren(node, symbols, imports, calls, scopeStack);
            return;
        }
        // ── New expressions (e.g., new Calculator()) ──
        case "new_expression": {
            // The constructor name
            for (const child of node.children) {
                if (child.type === "identifier") {
                    calls.push({
                        callee: `new ${child.text}`,
                        lineNumber: node.startPosition.row + 1,
                        scopePath: [...scopeStack],
                    });
                    break;
                }
            }
            walkTSChildren(node, symbols, imports, calls, scopeStack);
            return;
        }
        default:
            break;
    }
    // Default: recurse into children
    walkTSChildren(node, symbols, imports, calls, scopeStack);
}
function walkTSChildren(node, symbols, imports, calls, scopeStack) {
    for (const child of node.children) {
        walkTSNode(child, symbols, imports, calls, scopeStack);
    }
}
function extractCalleeName(node) {
    if (node.type === "identifier") {
        return node.text;
    }
    if (node.type === "member_expression") {
        const objectNode = node.childForFieldName("object");
        const propertyNode = node.childForFieldName("property");
        if (objectNode && propertyNode) {
            return `${objectNode.text}.${propertyNode.text}`;
        }
    }
    return null;
}
function extractTSFunctionSignature(node) {
    const nameNode = node.childForFieldName("name");
    const paramsNode = node.childForFieldName("parameters");
    const returnType = node.childForFieldName("return_type");
    let sig = `function ${nameNode?.text ?? "anonymous"}`;
    if (paramsNode)
        sig += paramsNode.text;
    if (returnType)
        sig += returnType.text;
    return sig;
}
function extractTSMethodSignature(node, enclosingClass) {
    const nameNode = node.childForFieldName("name");
    const paramsNode = node.childForFieldName("parameters");
    const prefix = enclosingClass ? `${enclosingClass}.` : "";
    let sig = `${prefix}${nameNode?.text ?? "anonymous"}`;
    if (paramsNode)
        sig += paramsNode.text;
    return sig;
}
// ─── Python walker ──────────────────────────────────────────────────
function walkPython(node, symbols, imports, calls, _source) {
    walkPythonNode(node, symbols, imports, calls, []);
}
function walkPythonNode(node, symbols, imports, calls, scopeStack) {
    switch (node.type) {
        // ── Function definitions ──
        case "function_definition": {
            const nameNode = node.childForFieldName("name");
            if (nameNode) {
                const funcName = nameNode.text;
                const isMethod = scopeStack.length > 0;
                const kind = isMethod ? "method" : "function";
                symbols.push({
                    // Emit the bare name; scopePath carries enclosing class(es).
                    name: funcName,
                    kind,
                    lineStart: node.startPosition.row + 1,
                    lineEnd: node.endPosition.row + 1,
                    signature: extractPythonFuncSignature(node, scopeStack[scopeStack.length - 1] ?? null),
                    scopePath: [...scopeStack],
                });
            }
            // Walk body for calls
            walkPythonChildren(node, symbols, imports, calls, scopeStack);
            return;
        }
        // ── Class definitions ──
        case "class_definition": {
            const nameNode = node.childForFieldName("name");
            if (nameNode) {
                const className = nameNode.text;
                symbols.push({
                    name: className,
                    kind: "class",
                    lineStart: node.startPosition.row + 1,
                    lineEnd: node.endPosition.row + 1,
                    signature: `class ${className}`,
                    scopePath: [...scopeStack],
                });
                // Walk body with class pushed onto scope stack
                const body = node.childForFieldName("body");
                if (body) {
                    walkPythonChildren(body, symbols, imports, calls, [
                        ...scopeStack,
                        className,
                    ]);
                }
            }
            return;
        }
        // ── import_statement: import os, import sys ──
        case "import_statement": {
            for (const child of node.children) {
                if (child.type === "dotted_name") {
                    imports.push(child.text);
                }
            }
            return;
        }
        // ── import_from_statement: from utils import X ──
        case "import_from_statement": {
            // The module name is the dotted_name right after "from"
            const moduleParts = [];
            let foundFrom = false;
            let foundImport = false;
            for (const child of node.children) {
                if (child.type === "from") {
                    foundFrom = true;
                    continue;
                }
                if (child.type === "import") {
                    foundImport = true;
                    continue;
                }
                if (foundFrom && !foundImport && child.type === "dotted_name") {
                    moduleParts.push(child.text);
                }
            }
            if (moduleParts.length > 0) {
                imports.push(moduleParts[0]);
            }
            return;
        }
        // ── Call expressions ──
        case "call": {
            const funcNode = node.childForFieldName("function");
            if (funcNode) {
                const callee = extractPythonCalleeName(funcNode);
                if (callee) {
                    calls.push({
                        callee,
                        lineNumber: node.startPosition.row + 1,
                        scopePath: [...scopeStack],
                    });
                }
            }
            // Walk arguments for nested calls
            walkPythonChildren(node, symbols, imports, calls, scopeStack);
            return;
        }
        default:
            break;
    }
    walkPythonChildren(node, symbols, imports, calls, scopeStack);
}
function walkPythonChildren(node, symbols, imports, calls, scopeStack) {
    for (const child of node.children) {
        walkPythonNode(child, symbols, imports, calls, scopeStack);
    }
}
function extractPythonCalleeName(node) {
    if (node.type === "identifier") {
        return node.text;
    }
    if (node.type === "attribute") {
        // e.g., logger.log -> attribute { identifier "logger", ".", identifier "log" }
        const objectNode = node.childForFieldName("object");
        const attrNode = node.childForFieldName("attribute");
        if (objectNode && attrNode) {
            return `${objectNode.text}.${attrNode.text}`;
        }
    }
    return null;
}
function extractPythonFuncSignature(node, enclosingClass) {
    const nameNode = node.childForFieldName("name");
    const paramsNode = node.childForFieldName("parameters");
    const prefix = enclosingClass ? `${enclosingClass}.` : "";
    let sig = `def ${prefix}${nameNode?.text ?? "anonymous"}`;
    if (paramsNode)
        sig += paramsNode.text;
    return sig;
}
// ─── Generic walker (fallback for other languages) ──────────────────
function walkGeneric(node, symbols, imports, calls, _source) {
    walkGenericNode(node, symbols, imports, calls);
}
function walkGenericNode(node, symbols, imports, calls) {
    // Try to extract function-like declarations
    if (node.type.includes("function_declaration") ||
        node.type.includes("function_definition") ||
        node.type.includes("method_declaration")) {
        const nameNode = node.childForFieldName("name");
        if (nameNode) {
            symbols.push({
                name: nameNode.text,
                kind: "function",
                lineStart: node.startPosition.row + 1,
                lineEnd: node.endPosition.row + 1,
                signature: nameNode.text,
                scopePath: [],
            });
        }
    }
    // Try to extract class-like declarations
    if (node.type.includes("class_declaration") ||
        node.type.includes("class_definition") ||
        node.type.includes("struct_item")) {
        const nameNode = node.childForFieldName("name");
        if (nameNode) {
            symbols.push({
                name: nameNode.text,
                kind: "class",
                lineStart: node.startPosition.row + 1,
                lineEnd: node.endPosition.row + 1,
                signature: nameNode.text,
                scopePath: [],
            });
        }
    }
    for (const child of node.children) {
        walkGenericNode(child, symbols, imports, calls);
    }
}
//# sourceMappingURL=parser.js.map