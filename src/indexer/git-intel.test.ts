import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { analyzeGitHistory } from "./git-intel.js";

describe("analyzeGitHistory", () => {
  let root: string;

  beforeEach(() => {
    // Deliberately include a space in the directory name.
    root = mkdtempSync(join(tmpdir(), "codemesh git intel "));
    execFileSync("git", ["init", "-q"], { cwd: root });
    execFileSync("git", ["config", "user.email", "test@example.com"], { cwd: root });
    execFileSync("git", ["config", "user.name", "Test"], { cwd: root });
    writeFileSync(join(root, "a.ts"), "export const a = 1;\n");
    execFileSync("git", ["add", "."], { cwd: root });
    execFileSync("git", ["commit", "-q", "-m", "initial"], { cwd: root });
  });

  afterEach(() => {
    rmSync(root, { recursive: true, force: true });
  });

  it("works inside a directory whose path contains a space", async () => {
    const result = await analyzeGitHistory(root, 10);
    expect(result.hotspots.length).toBeGreaterThan(0);
    expect(result.hotspots[0].path).toBe("a.ts");
  });
});
