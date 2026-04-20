#!/usr/bin/env node
import { z } from "zod";
import { createServer } from "./server.js";
type ServerInstance = ReturnType<typeof createServer>["server"];
type SmitheryContext = {
    config?: Partial<z.infer<typeof configSchema>>;
};
export declare const configSchema: z.ZodObject<{
    codemeshProjectRoot: z.ZodString;
}, z.core.$strip>;
export declare function createSandboxServer(): ServerInstance;
export default function createSmitheryServer({ config, }?: SmitheryContext): Promise<ServerInstance>;
export {};
