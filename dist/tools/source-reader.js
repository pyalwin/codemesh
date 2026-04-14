import { readFileSync } from "node:fs";
import { join } from "node:path";
/**
 * Read source lines from a file.
 * Returns the source code between lineStart and lineEnd (1-indexed, inclusive).
 * Returns null if file can't be read.
 *
 * NOT used in default tool responses — agents should Read files themselves.
 * This is for trace tool only, where following a call chain benefits from
 * seeing the code inline.
 */
export function readSourceLines(projectRoot, filePath, lineStart, lineEnd) {
    try {
        const absPath = join(projectRoot, filePath);
        const content = readFileSync(absPath, "utf-8");
        const lines = content.split("\n");
        const start = Math.max(0, lineStart - 1);
        const end = Math.min(lines.length, lineEnd);
        return lines.slice(start, end).join("\n");
    }
    catch {
        return null;
    }
}
//# sourceMappingURL=source-reader.js.map