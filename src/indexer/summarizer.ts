/**
 * LLM-powered symbol summarization at index time.
 *
 * Groups symbols by file, sends one Claude Haiku call per file,
 * returns a Map of symbol ID → one-sentence summary.
 */

import Anthropic from "@anthropic-ai/sdk";
import { readSourceLines } from "../tools/source-reader.js";
import type { SymbolNode } from "../graph/types.js";

interface SummarizableSymbol {
  id: string;
  name: string;
  kind: string;
  signature: string;
  filePath: string;
  lineStart: number;
  lineEnd: number;
}

export async function summarizeSymbols(
  projectRoot: string,
  symbols: SummarizableSymbol[],
): Promise<Map<string, string>> {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    console.warn("Warning: ANTHROPIC_API_KEY not set — skipping symbol summarization");
    return new Map();
  }

  const client = new Anthropic({ apiKey });
  const summaries = new Map<string, string>();

  // Group symbols by file for batched LLM calls
  const byFile = new Map<string, SummarizableSymbol[]>();
  for (const sym of symbols) {
    const existing = byFile.get(sym.filePath) ?? [];
    existing.push(sym);
    byFile.set(sym.filePath, existing);
  }

  for (const [filePath, fileSymbols] of byFile) {
    try {
      const fileSummaries = await summarizeFileSymbols(client, projectRoot, filePath, fileSymbols);
      for (const [id, summary] of fileSummaries) {
        summaries.set(id, summary);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.warn(`Warning: summarization failed for ${filePath} — ${msg}`);
      // Continue with other files
    }
  }

  return summaries;
}

async function summarizeFileSymbols(
  client: Anthropic,
  projectRoot: string,
  filePath: string,
  symbols: SummarizableSymbol[],
  retries = 2,
): Promise<Map<string, string>> {
  // Build the prompt with each symbol's source
  const symbolBlocks = symbols.map((sym, i) => {
    const source = readSourceLines(projectRoot, sym.filePath, sym.lineStart, sym.lineEnd);
    return `[${i}] ${sym.kind} "${sym.name}" (${sym.signature})\n${source ?? "(source unavailable)"}`;
  }).join("\n\n---\n\n");

  const prompt = `You are summarizing code symbols for a knowledge graph. For each symbol below, write ONE concise sentence describing what it does (its purpose), not how it's implemented.

File: ${filePath}

${symbolBlocks}

Respond with a JSON array of strings, one summary per symbol, in the same order. Example:
["Validates invoice line items against GL account rules", "Resolves a GL account code from vendor category"]

Return ONLY the JSON array, no other text.`;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const response = await client.messages.create({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 1024,
        messages: [{ role: "user", content: prompt }],
      });

      const text = response.content[0].type === "text" ? response.content[0].text : "";
      const parsed = JSON.parse(text.trim());

      if (!Array.isArray(parsed) || parsed.length !== symbols.length) {
        throw new Error(`Expected ${symbols.length} summaries, got ${Array.isArray(parsed) ? parsed.length : "non-array"}`);
      }

      const result = new Map<string, string>();
      for (let i = 0; i < symbols.length; i++) {
        if (typeof parsed[i] === "string" && parsed[i].length > 0) {
          result.set(symbols[i].id, parsed[i]);
        }
      }
      return result;
    } catch (e) {
      if (attempt < retries) {
        // Exponential backoff for rate limits
        await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
        continue;
      }
      throw e;
    }
  }

  return new Map();
}
