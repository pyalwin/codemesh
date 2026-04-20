/**
 * Git Intelligence — Extracts co-change pairs and hotspots from git history.
 *
 * - Hotspots: files with high change frequency (churn)
 * - Co-change pairs: files that frequently change together in the same commits
 */
import { execFileSync } from "node:child_process";
/**
 * Analyze git history to extract hotspots and co-change pairs.
 *
 * @param projectRoot - The root directory of the git repository
 * @param maxCommits - Maximum number of commits to analyze (default 200)
 */
export async function analyzeGitHistory(projectRoot, maxCommits = 200) {
    let logOutput;
    try {
        logOutput = execFileSync("git", [
            "log",
            "--name-only",
            "--pretty=format:---COMMIT---%ai",
            "-n",
            String(maxCommits),
        ], {
            cwd: projectRoot,
            encoding: "utf-8",
            maxBuffer: 10 * 1024 * 1024,
            timeout: 30_000,
        });
    }
    catch {
        // Not a git repo or git not available
        return { hotspots: [], coChangePairs: [] };
    }
    // Parse commits
    const commits = [];
    const parts = logOutput.split("---COMMIT---");
    for (const part of parts) {
        const trimmed = part.trim();
        if (!trimmed)
            continue;
        const lines = trimmed.split("\n").map((l) => l.trim()).filter(Boolean);
        if (lines.length === 0)
            continue;
        // First line contains the date from %ai format
        const dateLine = lines[0];
        const files = lines.slice(1).filter((f) => f.length > 0 && !f.startsWith("---"));
        if (files.length === 0)
            continue;
        // Extract ISO date from git's %ai format (e.g. "2024-01-15 10:30:00 -0500")
        const dateMatch = dateLine.match(/(\d{4}-\d{2}-\d{2})/);
        const date = dateMatch ? dateMatch[1] : new Date().toISOString().slice(0, 10);
        commits.push({ date, files });
    }
    // Count per-file change frequency and track last changed date
    const fileChangeCount = new Map();
    const fileLastChanged = new Map();
    for (const commit of commits) {
        for (const file of commit.files) {
            fileChangeCount.set(file, (fileChangeCount.get(file) ?? 0) + 1);
            const existing = fileLastChanged.get(file);
            if (!existing || commit.date > existing) {
                fileLastChanged.set(file, commit.date);
            }
        }
    }
    // Build hotspots sorted by changeCount descending
    const hotspots = Array.from(fileChangeCount.entries())
        .map(([path, changeCount]) => ({
        path,
        changeCount,
        lastChanged: fileLastChanged.get(path) ?? "",
    }))
        .sort((a, b) => b.changeCount - a.changeCount);
    // Count pairwise co-occurrences
    const pairCount = new Map();
    for (const commit of commits) {
        const files = commit.files;
        if (files.length < 2)
            continue;
        // Generate all pairs (sorted to ensure consistent key)
        for (let i = 0; i < files.length; i++) {
            for (let j = i + 1; j < files.length; j++) {
                const [a, b] = files[i] < files[j] ? [files[i], files[j]] : [files[j], files[i]];
                const key = `${a}\0${b}`;
                pairCount.set(key, (pairCount.get(key) ?? 0) + 1);
            }
        }
    }
    // Filter and build co-change pairs
    const coChangePairs = Array.from(pairCount.entries())
        .map(([key, count]) => {
        const [fileA, fileB] = key.split("\0");
        const changesA = fileChangeCount.get(fileA) ?? 1;
        const changesB = fileChangeCount.get(fileB) ?? 1;
        const confidence = count / Math.min(changesA, changesB);
        return { fileA, fileB, coChangeCount: count, confidence };
    })
        .filter((p) => p.coChangeCount >= 3 && p.confidence >= 0.3)
        .sort((a, b) => b.coChangeCount - a.coChangeCount);
    return { hotspots, coChangePairs };
}
//# sourceMappingURL=git-intel.js.map