/**
 * codemesh_workflow — Create a workflow node linking files in sequence.
 */
import type { StorageBackend } from "../graph/storage.js";
export interface WorkflowInput {
    name: string;
    description: string;
    files: string[];
}
export interface WorkflowOutput {
    workflowId: string;
    success: boolean;
}
export declare function handleWorkflow(storage: StorageBackend, input: WorkflowInput): Promise<WorkflowOutput>;
