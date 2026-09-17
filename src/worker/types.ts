import type { ReadinessResult } from '@agent-tool-platform/runtime/lifecycle';
import type { ToolInvocationContext } from '@agent-tool-platform/runtime/tools';
import type { z } from 'zod';

export type VisionWorkerOperation =
  'analyze_image' | 'extract_text_and_layout' | 'compare_images' | 'optimize_image_region';

export interface VisionWorker {
  invoke<TOutput>(
    operation: VisionWorkerOperation,
    input: unknown,
    outputSchema: z.ZodType<TOutput>,
    context: ToolInvocationContext,
  ): Promise<TOutput>;
  readiness(): Promise<ReadinessResult>;
  drain(): Promise<void>;
}
