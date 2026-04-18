/**
 * Language registry mapping file extensions to tree-sitter grammars.
 */

export interface LanguageConfig {
  name: string;
  extensions: string[];
  grammarPackage: string;
  /** Relative to src/indexer/queries/, e.g. "swift.scm". Absent = use walker. */
  queryFile?: string;
}

const LANGUAGE_CONFIGS: LanguageConfig[] = [
  {
    name: "typescript",
    extensions: [".ts", ".tsx"],
    grammarPackage: "tree-sitter-typescript",
  },
  {
    name: "javascript",
    extensions: [".js", ".jsx", ".mjs", ".cjs"],
    grammarPackage: "tree-sitter-javascript",
  },
  {
    name: "python",
    extensions: [".py", ".pyi"],
    grammarPackage: "tree-sitter-python",
  },
  {
    name: "go",
    extensions: [".go"],
    grammarPackage: "tree-sitter-go",
    queryFile: "go.scm",
  },
  {
    name: "rust",
    extensions: [".rs"],
    grammarPackage: "tree-sitter-rust",
  },
  {
    name: "java",
    extensions: [".java"],
    grammarPackage: "tree-sitter-java",
  },
  {
    name: "swift",
    extensions: [".swift"],
    grammarPackage: "tree-sitter-swift",
    queryFile: "swift.scm",
  },
  {
    name: "c",
    extensions: [".c", ".h"],
    grammarPackage: "tree-sitter-c",
  },
  {
    name: "cpp",
    extensions: [".cpp", ".cc", ".cxx", ".hpp", ".hh"],
    grammarPackage: "tree-sitter-cpp",
  },
];

/** Map from extension -> config for O(1) lookup */
const extensionMap = new Map<string, LanguageConfig>();
for (const config of LANGUAGE_CONFIGS) {
  for (const ext of config.extensions) {
    extensionMap.set(ext, config);
  }
}

/**
 * Returns the language config for a given file path based on its extension,
 * or null if the language is unsupported.
 */
export function getLanguageConfig(filePath: string): LanguageConfig | null {
  const dotIndex = filePath.lastIndexOf(".");
  if (dotIndex === -1) return null;
  const ext = filePath.slice(dotIndex);
  return extensionMap.get(ext) ?? null;
}

/**
 * Returns all file extensions supported by the language registry.
 */
export function getSupportedExtensions(): string[] {
  return Array.from(extensionMap.keys());
}
