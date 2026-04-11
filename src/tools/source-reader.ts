import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Read source lines from a file.
 * Returns the COMPLETE source code between lineStart and lineEnd (1-indexed, inclusive).
 * No truncation — the full function/class body is returned so the agent
 * doesn't need a separate Read call.
 */
export function readSourceLines(
  projectRoot: string,
  filePath: string,
  lineStart: number,
  lineEnd: number,
): string | null {
  try {
    const absPath = join(projectRoot, filePath);
    const content = readFileSync(absPath, "utf-8");
    const lines = content.split("\n");
    const start = Math.max(0, lineStart - 1);
    const end = Math.min(lines.length, lineEnd);
    return lines.slice(start, end).join("\n");
  } catch {
    return null;
  }
}

/**
 * Read the full file content.
 * No truncation — returns everything so the agent trusts the result.
 */
export function readFileContent(
  projectRoot: string,
  filePath: string,
): string | null {
  try {
    const absPath = join(projectRoot, filePath);
    return readFileSync(absPath, "utf-8");
  } catch {
    return null;
  }
}
