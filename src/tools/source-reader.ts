import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Read source lines from a file.
 * Returns the source code between lineStart and lineEnd (1-indexed, inclusive).
 * Returns null if file can't be read.
 */
export function readSourceLines(
  projectRoot: string,
  filePath: string,
  lineStart: number,
  lineEnd: number,
  maxLines: number = 50,
): string | null {
  try {
    const absPath = join(projectRoot, filePath);
    const content = readFileSync(absPath, "utf-8");
    const lines = content.split("\n");
    const start = Math.max(0, lineStart - 1);
    const end = Math.min(lines.length, lineEnd);
    const slice = lines.slice(start, end);

    if (slice.length > maxLines) {
      return slice.slice(0, maxLines).join("\n") + `\n... (${slice.length - maxLines} more lines)`;
    }
    return slice.join("\n");
  } catch {
    return null;
  }
}

/**
 * Read the full file content (for smaller files).
 */
export function readFileContent(
  projectRoot: string,
  filePath: string,
  maxLines: number = 200,
): string | null {
  try {
    const absPath = join(projectRoot, filePath);
    const content = readFileSync(absPath, "utf-8");
    const lines = content.split("\n");
    if (lines.length > maxLines) {
      return lines.slice(0, maxLines).join("\n") + `\n... (${lines.length - maxLines} more lines)`;
    }
    return content;
  } catch {
    return null;
  }
}
