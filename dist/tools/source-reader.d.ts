/**
 * Read source lines from a file.
 * Returns the source code between lineStart and lineEnd (1-indexed, inclusive).
 * Returns null if file can't be read.
 *
 * NOT used in default tool responses — agents should Read files themselves.
 * This is for trace tool only, where following a call chain benefits from
 * seeing the code inline.
 */
export declare function readSourceLines(projectRoot: string, filePath: string, lineStart: number, lineEnd: number): string | null;
