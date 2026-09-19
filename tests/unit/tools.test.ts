import { createTestInvocationContext } from '@agent-tool-platform/testkit';
import { describe, expect, it } from 'vitest';
import {
  analyzeImageInputSchema,
  analyzeImageTool,
  capabilityTools,
  compareImagesTool,
  extractTextInputSchema,
  extractTextTool,
  optimizeImageTool,
} from '../../src/tools/definitions.js';
import { FakeVisionWorker, sampleAnalysis } from '../helpers/fake-worker.js';

describe('vision tool contracts', () => {
  it('delegates structured image analysis to the Python worker', async () => {
    const worker = new FakeVisionWorker();
    const context = createTestInvocationContext();
    const output = await analyzeImageTool.handler(
      {
        image: { kind: 'local_path', path: '/allowed/portal.svg' },
        maxFacts: 50,
        processingMode: 'auto',
      },
      { worker },
      context,
    );

    expect(output).toEqual(sampleAnalysis);
    expect(worker.calls).toEqual([
      {
        operation: 'analyze_image',
        input: {
          image: { kind: 'local_path', path: '/allowed/portal.svg' },
          maxFacts: 50,
          processingMode: 'auto',
        },
        context,
      },
    ]);
  });

  it('rejects remote, inline, unknown, and unbounded inputs', () => {
    expect(
      analyzeImageInputSchema.safeParse({ image: { kind: 'url', url: 'https://example.invalid' } })
        .success,
    ).toBe(false);
    expect(
      analyzeImageInputSchema.safeParse({ image: { kind: 'base64', data: 'AAAA' } }).success,
    ).toBe(false);
    expect(
      analyzeImageInputSchema.safeParse({
        image: { kind: 'local_path', path: '/allowed/image.png' },
        language: 'klingon',
      }).success,
    ).toBe(false);
    expect(
      analyzeImageInputSchema.safeParse({
        image: { kind: 'local_path', path: '/allowed/image.png' },
        maxFacts: 101,
      }).success,
    ).toBe(false);
    expect(
      extractTextInputSchema.safeParse({
        image: { kind: 'local_path', path: '/allowed/image.png' },
        unexpected: true,
      }).success,
    ).toBe(false);
  });

  it('publishes truthful routing and side-effect annotations', () => {
    expect(capabilityTools.map((tool) => tool.name)).toEqual([
      'analyze_image',
      'extract_text_and_layout',
      'compare_images',
      'optimize_image_region',
    ]);
    expect(analyzeImageTool.routing.changesState).toBe(false);
    expect(extractTextTool.routing.changesState).toBe(false);
    expect(compareImagesTool.routing.changesState).toBe(true);
    expect(optimizeImageTool.routing.changesState).toBe(true);
    expect(compareImagesTool.annotations?.destructiveHint).toBe(false);
    expect(optimizeImageTool.annotations?.destructiveHint).toBe(false);
    expect(compareImagesTool.annotations?.idempotentHint).toBe(false);
    expect(optimizeImageTool.annotations?.readOnlyHint).toBe(false);
    expect(analyzeImageTool.routing.doNotUseWhen.join(' ')).toContain('document');
  });
});
