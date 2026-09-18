import { dirname, join } from 'node:path';
import { Readable } from 'node:stream';
import { fileURLToPath } from 'node:url';
import {
  AppError,
  BoundedQueue,
  buildChildEnvironment,
  ExecutableResolutionError,
  processFailureToAppError,
  resolveExecutable,
  runBoundedProcess,
  type BoundedProcessResult,
  type BoundedProcessSpec,
} from '@agent-tool-platform/runtime';
import type { CapabilityContext } from '@agent-tool-platform/runtime/capability';
import { readinessNotReady, type ReadinessResult } from '@agent-tool-platform/runtime/lifecycle';
import type { ToolInvocationContext } from '@agent-tool-platform/runtime/tools';
import { z } from 'zod';
import type { VisionConfig } from '../config.js';
import type { VisionWorker, VisionWorkerOperation } from './types.js';

const workerProtocolVersion = 1;
const maximumRequestBytes = 65_536;
const maximumStderrBytes = 8192;

const workerErrorSchema = z
  .object({
    code: z.enum([
      'invalid_input',
      'unauthorized',
      'forbidden',
      'not_found',
      'payload_too_large',
      'unsupported_media',
      'quota_exceeded',
      'busy',
      'timeout',
      'provider_unavailable',
      'provider_error',
      'internal',
    ]),
    message: z.string().min(1).max(300),
    retryable: z.boolean(),
    details: z.record(z.string(), z.string().max(300)).default({}),
  })
  .strict();

const workerEnvelopeSchema = z.discriminatedUnion('ok', [
  z
    .object({
      protocolVersion: z.literal(workerProtocolVersion),
      ok: z.literal(true),
      result: z.unknown(),
    })
    .strict(),
  z
    .object({
      protocolVersion: z.literal(workerProtocolVersion),
      ok: z.literal(false),
      error: workerErrorSchema,
    })
    .strict(),
]);

const workerHealthSchema = z
  .object({
    state: z.enum(['ready', 'degraded', 'not_ready']),
    detail: z.string().min(1).max(200),
  })
  .strict();

type ProcessRunner = (spec: BoundedProcessSpec) => Promise<BoundedProcessResult>;

interface PythonVisionWorkerOptions {
  readonly executablePath: string;
  readonly cwd: string;
  readonly env: Record<string, string>;
  readonly concurrency: number;
  readonly queueDepth: number;
  readonly timeoutMs: number;
  readonly maxOutputBytes: number;
  readonly runner?: ProcessRunner;
}

interface VisionChildEnvironmentOptions {
  readonly executablePath: string;
  readonly workspacePath: string;
  readonly moduleRoot: string;
  readonly assetRoot: string;
  readonly workerEnvironment: Readonly<Record<string, string>>;
}

export const buildVisionChildEnvironment = ({
  executablePath,
  workspacePath,
  moduleRoot,
  assetRoot,
  workerEnvironment,
}: VisionChildEnvironmentOptions): Record<string, string> =>
  buildChildEnvironment({
    pathEntries: [dirname(executablePath)],
    tempDir: workspacePath,
    extra: {
      ...workerEnvironment,
      VISION_ASSET_ROOT: assetRoot,
      VISION_LOG_LEVEL: 'ERROR',
      PADDLE_PDX_CACHE_HOME: join(workspacePath, 'paddlex-cache'),
      USERPROFILE: workspacePath,
      XDG_CACHE_HOME: join(workspacePath, 'cache'),
      PYTHONPATH: moduleRoot,
      PYTHONDONTWRITEBYTECODE: '1',
      PYTHONNOUSERSITE: '1',
      PYTHONUNBUFFERED: '1',
    },
  });

const mapWorkerError = (error: z.infer<typeof workerErrorSchema>): AppError => {
  const code = {
    invalid_input: 'bad_request',
    unauthorized: 'unauthorized',
    forbidden: 'forbidden',
    not_found: 'not_found',
    payload_too_large: 'limit_exceeded',
    unsupported_media: 'bad_request',
    quota_exceeded: 'rate_limited',
    busy: 'busy',
    timeout: 'timeout',
    provider_unavailable: 'upstream_error',
    provider_error: 'upstream_error',
    internal: 'internal_error',
  } as const;
  return new AppError(code[error.code], error.message, error.details, error.retryable);
};

export class PythonVisionWorker implements VisionWorker {
  private readonly queue: BoundedQueue;
  private readonly runner: ProcessRunner;
  private shutdown: Promise<void> | undefined;

  public constructor(private readonly options: PythonVisionWorkerOptions) {
    this.queue = new BoundedQueue(
      options.concurrency,
      options.queueDepth,
      'vision worker requests',
    );
    this.runner = options.runner ?? runBoundedProcess;
  }

  public invoke<TOutput>(
    operation: VisionWorkerOperation,
    input: unknown,
    outputSchema: z.ZodType<TOutput>,
    context: ToolInvocationContext,
  ): Promise<TOutput> {
    return this.queue.run(
      () =>
        this.execute(
          operation,
          input,
          outputSchema,
          context.requestId,
          context.principal.id,
          context.signal,
        ),
      context.signal,
    );
  }

  public async readiness(): Promise<ReadinessResult> {
    if (this.queue.stats.closed) {
      return readinessNotReady('python-worker', 'the worker queue is draining');
    }
    try {
      const result = await this.queue.run(() =>
        this.execute('health', {}, workerHealthSchema, 'readiness', 'readiness'),
      );
      return { name: 'python-worker', state: result.state, detail: result.detail };
    } catch {
      return readinessNotReady('python-worker', 'the worker readiness probe failed');
    }
  }

  public drain(): Promise<void> {
    this.shutdown ??= this.queue.drain();
    return this.shutdown;
  }

  private async execute<TOutput>(
    operation: VisionWorkerOperation | 'health',
    input: unknown,
    outputSchema: z.ZodType<TOutput>,
    requestId: string,
    principal: string,
    signal?: AbortSignal,
  ): Promise<TOutput> {
    const request = `${JSON.stringify({
      protocolVersion: workerProtocolVersion,
      operation,
      requestId,
      principal,
      input,
    })}\n`;
    if (Buffer.byteLength(request, 'utf8') > maximumRequestBytes) {
      throw new AppError('limit_exceeded', 'Vision worker request exceeds the protocol limit', {
        maxBytes: maximumRequestBytes,
      });
    }
    const result = await this.runner({
      executablePath: this.options.executablePath,
      label: 'Python vision worker',
      args: ['-B', '-m', 'vision_server.worker'],
      cwd: this.options.cwd,
      env: this.options.env,
      timeoutMs: this.options.timeoutMs,
      maxOutputBytes: this.options.maxOutputBytes,
      maxStderrBytes: maximumStderrBytes,
      stdin: Readable.from([request]),
      signal,
    });
    const processError = processFailureToAppError(result, 'Python vision worker');
    if (processError) throw processError;
    if (result.code !== 0) {
      throw new AppError('upstream_error', 'Python vision worker exited without a valid response', {
        exitCode: result.code,
        signal: result.terminationSignal,
      });
    }

    let decoded: unknown;
    try {
      decoded = JSON.parse(result.stdout);
    } catch (error) {
      throw new AppError(
        'upstream_error',
        'Python vision worker returned malformed protocol output',
        undefined,
        false,
        error,
      );
    }
    const envelope = workerEnvelopeSchema.safeParse(decoded);
    if (!envelope.success) {
      throw new AppError(
        'upstream_error',
        'Python vision worker returned an invalid protocol envelope',
      );
    }
    if (!envelope.data.ok) throw mapWorkerError(envelope.data.error);
    const parsed = outputSchema.safeParse(envelope.data.result);
    if (!parsed.success) {
      throw new AppError(
        'upstream_error',
        'Python vision worker returned a result outside the capability contract',
      );
    }
    return parsed.data;
  }
}

const resolvePython = async (override: string | undefined): Promise<string> => {
  if (override) return resolveExecutable('python', { override });
  const names = process.platform === 'win32' ? ['python'] : ['python3', 'python'];
  let lastError: unknown;
  for (const name of names) {
    try {
      return await resolveExecutable(name);
    } catch (error) {
      if (!(error instanceof ExecutableResolutionError)) throw error;
      lastError = error;
    }
  }
  throw new ExecutableResolutionError('Python 3 was not found on an absolute PATH entry', {
    cause: lastError,
  });
};

export const createPythonVisionWorker = async (
  context: CapabilityContext<VisionConfig>,
): Promise<VisionWorker> => {
  const workspace = await context.createScratchWorkspace({ prefix: 'vision-worker-' });
  const workspacePath = workspace.path;
  const executablePath = await resolvePython(context.config.vision.pythonPath);
  const moduleRoot = fileURLToPath(new URL('../../src', import.meta.url));
  const assetRoot = context.config.vision.assetRoot ?? join(workspacePath, 'assets');
  const env = buildVisionChildEnvironment({
    executablePath,
    workspacePath,
    moduleRoot,
    assetRoot,
    workerEnvironment: context.config.vision.workerEnvironment,
  });
  return new PythonVisionWorker({
    executablePath,
    cwd: workspacePath,
    env,
    concurrency: context.config.vision.concurrency,
    queueDepth: context.config.vision.queueDepth,
    timeoutMs: context.config.vision.workerTimeoutMs,
    maxOutputBytes: context.config.vision.maxOutputBytes,
  });
};
