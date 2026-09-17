import { join } from 'node:path';
import type { BoundedProcessResult, BoundedProcessSpec } from '@agent-tool-platform/runtime';
import { createTestInvocationContext } from '@agent-tool-platform/testkit';
import { z } from 'zod';
import { describe, expect, it } from 'vitest';
import { buildVisionChildEnvironment, PythonVisionWorker } from '../../src/worker/client.js';

const outputSchema = z.object({ value: z.string() }).strict();

const result = (overrides: Partial<BoundedProcessResult> = {}): BoundedProcessResult => ({
  code: 0,
  terminationSignal: null,
  stdout: JSON.stringify({ protocolVersion: 1, ok: true, result: { value: 'ok' } }),
  stderr: '',
  stdoutBytes: 64,
  stdinBytes: 64,
  timedOut: false,
  aborted: false,
  outputLimitReached: false,
  stoppedEarly: false,
  durationMs: 1,
  ...overrides,
});

const worker = (
  runner: (spec: BoundedProcessSpec) => Promise<BoundedProcessResult>,
  overrides: { concurrency?: number; queueDepth?: number; dispose?: () => Promise<void> } = {},
) =>
  new PythonVisionWorker({
    executablePath: process.execPath,
    cwd: process.cwd(),
    env: { PATH: process.cwd() },
    concurrency: overrides.concurrency ?? 1,
    queueDepth: overrides.queueDepth ?? 1,
    timeoutMs: 1000,
    maxOutputBytes: 4096,
    runner,
    ...(overrides.dispose ? { dispose: overrides.dispose } : {}),
  });

describe('Python worker adapter', () => {
  it('isolates Paddle and generic caches beneath the private workspace', () => {
    const workspacePath = join(process.cwd(), 'private-worker');
    const env = buildVisionChildEnvironment({
      executablePath: process.execPath,
      workspacePath,
      moduleRoot: join(process.cwd(), 'src'),
      assetRoot: join(workspacePath, 'assets'),
      workerEnvironment: { VISION_ALLOWED_ROOTS: process.cwd() },
    });
    expect(env['PADDLE_PDX_CACHE_HOME']).toBe(join(workspacePath, 'paddlex-cache'));
    expect(env['USERPROFILE']).toBe(workspacePath);
    expect(env['XDG_CACHE_HOME']).toBe(join(workspacePath, 'cache'));
  });

  it('uses fixed argv, bounded execution, stdin, and validated protocol output', async () => {
    let captured: BoundedProcessSpec | undefined;
    const client = worker(async (spec) => {
      captured = spec;
      return result();
    });
    await expect(
      client.invoke('analyze_image', {}, outputSchema, createTestInvocationContext()),
    ).resolves.toEqual({ value: 'ok' });
    expect(captured?.args).toEqual(['-B', '-m', 'vision_server.worker']);
    expect(captured?.timeoutMs).toBe(1000);
    expect(captured?.maxOutputBytes).toBe(4096);
    expect(captured?.stdin).toBeDefined();
    await client.drain();
  });

  it('maps worker errors without exposing stderr', async () => {
    const client = worker(async () =>
      result({
        stdout: JSON.stringify({
          protocolVersion: 1,
          ok: false,
          error: {
            code: 'payload_too_large',
            message: 'Image is too large',
            retryable: false,
            details: { maxBytes: '100' },
          },
        }),
        stderr: 'private path must not escape',
      }),
    );
    await expect(
      client.invoke('analyze_image', {}, outputSchema, createTestInvocationContext()),
    ).rejects.toMatchObject({ code: 'limit_exceeded', message: 'Image is too large' });
    await client.drain();
  });

  it('rejects malformed, contaminated, and contract-invalid worker output', async () => {
    for (const stdout of [
      'log line\n{"protocolVersion":1,"ok":true,"result":{"value":"ok"}}',
      '{"protocolVersion":1,"ok":true,"result":{"wrong":true}}',
      '{"protocolVersion":2,"ok":true,"result":{"value":"ok"}}',
    ]) {
      const client = worker(async () => result({ stdout }));
      await expect(
        client.invoke('analyze_image', {}, outputSchema, createTestInvocationContext()),
      ).rejects.toMatchObject({ code: 'upstream_error' });
      await client.drain();
    }
  });

  it('maps timeout and cancellation outcomes from the Platform process primitive', async () => {
    const timedOut = worker(async () => result({ timedOut: true, code: null }));
    await expect(
      timedOut.invoke('analyze_image', {}, outputSchema, createTestInvocationContext()),
    ).rejects.toMatchObject({ code: 'timeout', retryable: true });
    await timedOut.drain();

    const aborted = worker(async () => result({ aborted: true, code: null }));
    await expect(
      aborted.invoke('analyze_image', {}, outputSchema, createTestInvocationContext()),
    ).rejects.toMatchObject({ code: 'busy', retryable: true });
    await aborted.drain();
  });

  it('drains queued work and disposes its private workspace exactly once', async () => {
    let release = (): void => undefined;
    let disposed = 0;
    const client = worker(
      () =>
        new Promise((resolve) => {
          release = () => resolve(result());
        }),
      { queueDepth: 0, dispose: async () => void (disposed += 1) },
    );
    const pending = client.invoke('analyze_image', {}, outputSchema, createTestInvocationContext());
    await Promise.resolve();
    const draining = client.drain();
    release();
    await pending;
    await draining;
    await client.drain();
    expect(disposed).toBe(1);
    await expect(
      client.invoke('analyze_image', {}, outputSchema, createTestInvocationContext()),
    ).rejects.toMatchObject({ code: 'busy' });
  });
});
