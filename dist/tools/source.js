/**
 * codemesh_source — Return source code for a single symbol by ID.
 */
import { join } from "node:path";
import { readSourceLines } from "./source-reader.js";
export async function handleSource(storage, input, projectRoot) {
    const node = await storage.getNode(input.id);
    if (!node || node.type !== "symbol") {
        throw new Error(`Symbol not found: ${input.id}`);
    }
    const sym = node;
    const source = readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd);
    if (source === null) {
        throw new Error(`Could not read source for ${sym.name} at ${sym.filePath}:${sym.lineStart}-${sym.lineEnd}`);
    }
    return {
        symbol: sym.name,
        id: sym.id,
        filePath: sym.filePath,
        absolutePath: join(projectRoot, sym.filePath),
        kind: sym.kind,
        signature: sym.signature,
        summary: sym.summary ?? null,
        lineStart: sym.lineStart,
        lineEnd: sym.lineEnd,
        source,
    };
}
//# sourceMappingURL=source.js.map