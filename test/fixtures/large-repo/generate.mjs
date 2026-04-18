#!/usr/bin/env node
// Generates N TypeScript files each with ~20 exported symbols.
// Usage: node generate.mjs [fileCount=1000]

import { mkdirSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const rootDir = join(__dirname, "src");
const fileCount = parseInt(process.argv[2] ?? "1000", 10);

if (existsSync(rootDir)) rmSync(rootDir, { recursive: true });
mkdirSync(rootDir, { recursive: true });

for (let i = 0; i < fileCount; i++) {
  const dir = join(rootDir, `pkg${Math.floor(i / 100)}`);
  mkdirSync(dir, { recursive: true });
  const lines = [
    `// auto-generated file ${i}`,
    `import { helper } from "../pkg0/mod0";`,
  ];
  for (let s = 0; s < 20; s++) {
    lines.push(
      `export function fn_${i}_${s}(x: number): number { return helper(x) + ${s}; }`,
    );
  }
  if (i === 0) {
    lines.push(`export function helper(x: number): number { return x + 1; }`);
  }
  writeFileSync(join(dir, `mod${i}.ts`), lines.join("\n") + "\n");
}

console.log(`Generated ${fileCount} files under ${rootDir}`);
