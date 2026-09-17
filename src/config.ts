import { delimiter, isAbsolute, resolve } from 'node:path';
import {
  ConfigurationError,
  defineCapabilityConfig,
  type PlatformConfig,
} from '@agent-tool-platform/runtime/config';
import { z } from 'zod';

export const supportedLanguages = ['en', 'ch', 'fr', 'german', 'japan', 'korean'] as const;

const languageSchema = z.enum(supportedLanguages);
const boundedInteger = (minimum: number, maximum: number, fallback: number) =>
  z.coerce.number().int().min(minimum).max(maximum).default(fallback);
const boundedNumber = (minimum: number, maximum: number, fallback: number) =>
  z.coerce.number().min(minimum).max(maximum).default(fallback);
const optionalPath = z.string().min(1).max(4096).optional();
const optionalValue = z.string().min(1).max(4096).optional();

const languageListSchema = z
  .string()
  .default('en')
  .transform((value, context): readonly (typeof supportedLanguages)[number][] => {
    const entries = [
      ...new Set(
        value
          .split(',')
          .map((entry) => entry.trim())
          .filter(Boolean),
      ),
    ];
    const parsed = z.array(languageSchema).min(1).max(supportedLanguages.length).safeParse(entries);
    if (!parsed.success) {
      context.addIssue({ code: 'custom', message: 'Contains an unsupported OCR language' });
      return z.NEVER;
    }
    return parsed.data;
  });

export const visionEnvironmentSchema = z.object({
  VISION_PYTHON_PATH: optionalPath,
  VISION_ALLOWED_ROOTS: z.string().min(1).max(16_384),
  VISION_MAX_IMAGE_BYTES: boundedInteger(1024, 64 * 1024 * 1024, 10 * 1024 * 1024),
  VISION_MAX_IMAGE_PIXELS: boundedInteger(1024, 200_000_000, 40_000_000),
  VISION_PROVIDER_MODE: z.enum(['local', 'azure', 'auto']).default('local'),
  VISION_DEFAULT_LANGUAGE: languageSchema.default('en'),
  VISION_PADDLE_LANGUAGES: languageListSchema,
  VISION_PADDLE_CACHE_SIZE: boundedInteger(1, 8, 2),
  VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT: z.url().startsWith('https://').optional(),
  VISION_AZURE_CONTENT_UNDERSTANDING_API_VERSION: z.string().min(1).max(40).default('2025-11-01'),
  VISION_AZURE_CONTENT_UNDERSTANDING_ANALYZER: z
    .string()
    .min(1)
    .max(120)
    .default('prebuilt-documentAnalyzer'),
  VISION_STORAGE_BACKEND: z.enum(['filesystem', 'azure_blob']).default('filesystem'),
  VISION_ASSET_ROOT: optionalPath,
  VISION_STORAGE_ACCOUNT_URL: z.url().startsWith('https://').optional(),
  VISION_ASSET_CONTAINER: z.string().min(1).max(128).optional(),
  VISION_ARTIFACT_CONTAINER: z.string().min(1).max(128).optional(),
  VISION_ASSET_TTL_SECONDS: boundedInteger(60, 7 * 24 * 3600, 3600),
  VISION_ASSET_MAX_BYTES: boundedInteger(1024, 64 * 1024 * 1024, 10 * 1024 * 1024),
  VISION_ASSET_QUOTA_BYTES: boundedInteger(1024, 2_147_483_647, 256 * 1024 * 1024),
  VISION_ASSET_QUOTA_COUNT: boundedInteger(1, 10_000, 200),
  VISION_MAX_CONCURRENCY: boundedInteger(1, 16, 2),
  VISION_MAX_QUEUE_DEPTH: boundedInteger(0, 128, 8),
  VISION_OPERATION_TIMEOUT_SECONDS: boundedNumber(0.1, 600, 60),
  VISION_PROVIDER_TIMEOUT_SECONDS: boundedNumber(0.1, 600, 30),
  VISION_SHUTDOWN_GRACE_SECONDS: boundedNumber(0, 120, 10),
  VISION_WORKER_TIMEOUT_MS: boundedInteger(1000, 610_000, 65_000),
  VISION_WORKER_MAX_OUTPUT_BYTES: boundedInteger(65_536, 8 * 1024 * 1024, 2 * 1024 * 1024),
  AZURE_TENANT_ID: optionalValue,
  AZURE_CLIENT_ID: optionalValue,
  AZURE_CLIENT_SECRET: optionalValue,
});

type VisionEnvironment = z.infer<typeof visionEnvironmentSchema>;

export interface VisionConfig extends PlatformConfig {
  readonly vision: {
    readonly pythonPath: string | undefined;
    readonly allowedRoots: readonly string[];
    readonly concurrency: number;
    readonly queueDepth: number;
    readonly workerTimeoutMs: number;
    readonly maxOutputBytes: number;
    readonly workerEnvironment: Readonly<Record<string, string>>;
    readonly assetRoot: string | undefined;
  };
}

const splitRoots = (value: string): readonly string[] => {
  const separator = value.includes(',') ? ',' : delimiter;
  const roots = [
    ...new Set(
      value
        .split(separator)
        .map((entry) => entry.trim())
        .filter(Boolean),
    ),
  ];
  if (roots.length === 0) throw new ConfigurationError('VISION_ALLOWED_ROOTS must not be empty');
  for (const root of roots) {
    if (!isAbsolute(root)) {
      throw new ConfigurationError('Every VISION_ALLOWED_ROOTS entry must be absolute');
    }
  }
  return roots.map((root) => resolve(root));
};

const copyOptional = (
  target: Record<string, string>,
  source: VisionEnvironment,
  key: keyof VisionEnvironment,
): void => {
  const value = source[key];
  if (typeof value === 'string') target[key] = value;
};

export const visionConfig = defineCapabilityConfig({
  schema: visionEnvironmentSchema,
  build({ base, env }): VisionConfig {
    const workerEnvironment: Record<string, string> = {
      VISION_ALLOWED_ROOTS: env.VISION_ALLOWED_ROOTS,
      VISION_MAX_IMAGE_BYTES: String(env.VISION_MAX_IMAGE_BYTES),
      VISION_MAX_IMAGE_PIXELS: String(env.VISION_MAX_IMAGE_PIXELS),
      VISION_PROVIDER_MODE: env.VISION_PROVIDER_MODE,
      VISION_DEFAULT_LANGUAGE: env.VISION_DEFAULT_LANGUAGE,
      VISION_PADDLE_LANGUAGES: env.VISION_PADDLE_LANGUAGES.join(','),
      VISION_PADDLE_CACHE_SIZE: String(env.VISION_PADDLE_CACHE_SIZE),
      VISION_AZURE_CONTENT_UNDERSTANDING_API_VERSION:
        env.VISION_AZURE_CONTENT_UNDERSTANDING_API_VERSION,
      VISION_AZURE_CONTENT_UNDERSTANDING_ANALYZER: env.VISION_AZURE_CONTENT_UNDERSTANDING_ANALYZER,
      VISION_STORAGE_BACKEND: env.VISION_STORAGE_BACKEND,
      VISION_ASSET_TTL_SECONDS: String(env.VISION_ASSET_TTL_SECONDS),
      VISION_ASSET_MAX_BYTES: String(env.VISION_ASSET_MAX_BYTES),
      VISION_ASSET_QUOTA_BYTES: String(env.VISION_ASSET_QUOTA_BYTES),
      VISION_ASSET_QUOTA_COUNT: String(env.VISION_ASSET_QUOTA_COUNT),
      VISION_MAX_CONCURRENCY: String(env.VISION_MAX_CONCURRENCY),
      VISION_MAX_QUEUE_DEPTH: String(env.VISION_MAX_QUEUE_DEPTH),
      VISION_OPERATION_TIMEOUT_SECONDS: String(env.VISION_OPERATION_TIMEOUT_SECONDS),
      VISION_PROVIDER_TIMEOUT_SECONDS: String(env.VISION_PROVIDER_TIMEOUT_SECONDS),
      VISION_SHUTDOWN_GRACE_SECONDS: String(env.VISION_SHUTDOWN_GRACE_SECONDS),
      VISION_WORKER_MAX_OUTPUT_BYTES: String(env.VISION_WORKER_MAX_OUTPUT_BYTES),
    };
    for (const key of [
      'VISION_ASSET_ROOT',
      'VISION_STORAGE_ACCOUNT_URL',
      'VISION_ASSET_CONTAINER',
      'VISION_ARTIFACT_CONTAINER',
    ] as const) {
      copyOptional(workerEnvironment, env, key);
    }
    if (env.VISION_PROVIDER_MODE !== 'local') {
      for (const key of [
        'VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT',
        'AZURE_TENANT_ID',
        'AZURE_CLIENT_ID',
        'AZURE_CLIENT_SECRET',
      ] as const) {
        copyOptional(workerEnvironment, env, key);
      }
    }
    return {
      ...base,
      vision: {
        pythonPath: env.VISION_PYTHON_PATH,
        allowedRoots: splitRoots(env.VISION_ALLOWED_ROOTS),
        concurrency: env.VISION_MAX_CONCURRENCY,
        queueDepth: env.VISION_MAX_QUEUE_DEPTH,
        workerTimeoutMs: env.VISION_WORKER_TIMEOUT_MS,
        maxOutputBytes: env.VISION_WORKER_MAX_OUTPUT_BYTES,
        workerEnvironment,
        assetRoot: env.VISION_ASSET_ROOT,
      },
    };
  },
  validate(config): void {
    const environment = config.vision.workerEnvironment;
    const providerMode = environment['VISION_PROVIDER_MODE'];
    if (
      providerMode !== 'local' &&
      environment['VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT'] === undefined
    ) {
      throw new ConfigurationError(
        'Azure and auto provider modes require VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT',
      );
    }
    if (
      providerMode !== 'local' &&
      ['AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET'].some(
        (key) => environment[key] === undefined,
      )
    ) {
      throw new ConfigurationError(
        'Azure and auto provider modes require AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET',
      );
    }
    if (environment['VISION_STORAGE_BACKEND'] === 'azure_blob') {
      if (
        environment['VISION_STORAGE_ACCOUNT_URL'] === undefined ||
        environment['VISION_ASSET_CONTAINER'] === undefined
      ) {
        throw new ConfigurationError(
          'The azure_blob backend requires VISION_STORAGE_ACCOUNT_URL and VISION_ASSET_CONTAINER',
        );
      }
    }
    const defaultLanguage = environment['VISION_DEFAULT_LANGUAGE'];
    const allowedLanguages = environment['VISION_PADDLE_LANGUAGES']?.split(',') ?? [];
    if (defaultLanguage === undefined || !allowedLanguages.includes(defaultLanguage)) {
      throw new ConfigurationError(
        'VISION_DEFAULT_LANGUAGE must be included in VISION_PADDLE_LANGUAGES',
      );
    }
    const operationTimeoutMs = Number(environment['VISION_OPERATION_TIMEOUT_SECONDS']) * 1000;
    const providerTimeoutMs = Number(environment['VISION_PROVIDER_TIMEOUT_SECONDS']) * 1000;
    if (providerTimeoutMs > operationTimeoutMs) {
      throw new ConfigurationError(
        'VISION_PROVIDER_TIMEOUT_SECONDS cannot exceed VISION_OPERATION_TIMEOUT_SECONDS',
      );
    }
    if (config.vision.workerTimeoutMs < operationTimeoutMs + 1000) {
      throw new ConfigurationError(
        'VISION_WORKER_TIMEOUT_MS must exceed the Python operation timeout by at least 1000ms',
      );
    }
    if (config.vision.pythonPath && !isAbsolute(config.vision.pythonPath)) {
      throw new ConfigurationError('VISION_PYTHON_PATH must be absolute');
    }
    if (config.vision.assetRoot && !isAbsolute(config.vision.assetRoot)) {
      throw new ConfigurationError('VISION_ASSET_ROOT must be absolute when configured');
    }
  },
});
