/**
 * Git Intelligence — Extracts co-change pairs and hotspots from git history.
 *
 * - Hotspots: files with high change frequency (churn)
 * - Co-change pairs: files that frequently change together in the same commits
 */
export interface GitIntelResult {
    hotspots: Array<{
        path: string;
        changeCount: number;
        lastChanged: string;
    }>;
    coChangePairs: Array<{
        fileA: string;
        fileB: string;
        coChangeCount: number;
        confidence: number;
    }>;
}
/**
 * Analyze git history to extract hotspots and co-change pairs.
 *
 * @param projectRoot - The root directory of the git repository
 * @param maxCommits - Maximum number of commits to analyze (default 200)
 */
export declare function analyzeGitHistory(projectRoot: string, maxCommits?: number): Promise<GitIntelResult>;
