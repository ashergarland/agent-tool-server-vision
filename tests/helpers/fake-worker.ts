import { readinessReady } from '@agent-tool-platform/runtime/lifecycle';
import type { ToolInvocationContext } from '@agent-tool-platform/runtime/tools';
import type { z } from 'zod';
import type { VisionWorker, VisionWorkerOperation } from '../../src/worker/types.js';

export const sampleAnalysis = {
  summary: 'revision: sample--rev-1; listener port: 3000',
  extractedText: 'sample / sample--rev-1\nserver_started port=3000',
  facts: [
    {
      kind: 'listener',
      label: 'listener port',
      value: '3000',
      evidence: 'server_started port=3000',
      confidence: 0.95,
      box: null,
      provenance: { source: 'embedded_svg_text', blockIds: ['b0001'] },
    },
  ],
  dimensions: { width: 400, height: 200 },
  provider: {
    name: 'embedded_svg_text',
    mode: 'local',
    model: 'svg-text-elements-v1',
    apiVersion: null,
  },
  analysisMode: 'embedded_svg_text',
  fallbackUsed: false,
  fallbackReason: null,
  meta: { warnings: [], truncated: false },
} as const;

export class FakeVisionWorker implements VisionWorker {
  public readonly calls: {
    readonly operation: VisionWorkerOperation;
    readonly input: unknown;
    readonly context: ToolInvocationContext;
  }[] = [];
  public drained = false;

  public constructor(
    private readonly responses: Partial<Record<VisionWorkerOperation, unknown>> = {
      analyze_image: sampleAnalysis,
    },
  ) {}

  public invoke<TOutput>(
    operation: VisionWorkerOperation,
    input: unknown,
    outputSchema: z.ZodType<TOutput>,
    context: ToolInvocationContext,
  ): Promise<TOutput> {
    this.calls.push({ operation, input, context });
    return Promise.resolve(outputSchema.parse(this.responses[operation]));
  }

  public readiness() {
    return Promise.resolve(readinessReady('python-worker', 'fake worker ready'));
  }

  public drain(): Promise<void> {
    this.drained = true;
    return Promise.resolve();
  }
}
