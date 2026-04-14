/**
 * Language registry mapping file extensions to tree-sitter grammars.
 */
export interface LanguageConfig {
    name: string;
    extensions: string[];
    grammarPackage: string;
}
/**
 * Returns the language config for a given file path based on its extension,
 * or null if the language is unsupported.
 */
export declare function getLanguageConfig(filePath: string): LanguageConfig | null;
/**
 * Returns all file extensions supported by the language registry.
 */
export declare function getSupportedExtensions(): string[];
