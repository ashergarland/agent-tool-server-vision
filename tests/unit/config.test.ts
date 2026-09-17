import { delimiter, join, resolve } from 'node:path';
import { loadCapabilityConfig } from '@agent-tool-platform/runtime/config';
import { describe, expect, it } from 'vitest';
import { visionConfig, type VisionConfig } from '../../src/config.js';

const load = (overrides: NodeJS.ProcessEnv = {}): VisionConfig =>
  loadCapabilityConfig({
    defaults: { serviceName: 'vision', serviceVersion: 'test' },
    spec: visionConfig,
    source: {
      NODE_ENV: 'test',
      AUTH_MODE: 'disabled',
      VISION_ALLOWED_ROOTS: process.cwd(),
      ...overrides,
    },
  });

describe('vision configuration', () => {
  it('builds bounded local defaults and a narrow worker environment', () => {
    const config = load({
      UNRELATED_SECRET: 'must-not-cross',
      VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT: 'https://unused.example.com',
      AZURE_TENANT_ID: 'must-not-cross',
      AZURE_CLIENT_ID: 'must-not-cross',
      AZURE_CLIENT_SECRET: 'must-not-cross',
    });
    expect(config.vision.allowedRoots).toEqual([resolve(process.cwd())]);
    expect(config.vision.concurrency).toBe(2);
    expect(config.vision.queueDepth).toBe(8);
    expect(config.vision.workerEnvironment).not.toHaveProperty('UNRELATED_SECRET');
    expect(config.vision.workerEnvironment).not.toHaveProperty('AZURE_CLIENT_SECRET');
    expect(config.vision.workerEnvironment['VISION_PROVIDER_MODE']).toBe('local');
  });

  it('accepts multiple absolute roots using the host separator', () => {
    const first = resolve(process.cwd());
    const second = resolve(join(process.cwd(), 'tests'));
    expect(
      load({ VISION_ALLOWED_ROOTS: `${first}${delimiter}${second}` }).vision.allowedRoots,
    ).toEqual([first, second]);
  });

  it('allows active-only admission with no queued worker requests', () => {
    const config = load({ VISION_MAX_QUEUE_DEPTH: '0' });
    expect(config.vision.queueDepth).toBe(0);
    expect(config.vision.workerEnvironment['VISION_MAX_QUEUE_DEPTH']).toBe('0');
  });

  it('rejects relative roots and inconsistent timeout or language policy', () => {
    expect(() => load({ VISION_ALLOWED_ROOTS: 'relative' })).toThrow(/absolute/u);
    expect(() =>
      load({
        VISION_PROVIDER_TIMEOUT_SECONDS: '61',
        VISION_OPERATION_TIMEOUT_SECONDS: '60',
      }),
    ).toThrow(/cannot exceed/u);
    expect(() => load({ VISION_DEFAULT_LANGUAGE: 'fr', VISION_PADDLE_LANGUAGES: 'en' })).toThrow(
      /must be included/u,
    );
    expect(() =>
      load({ VISION_OPERATION_TIMEOUT_SECONDS: '60', VISION_WORKER_TIMEOUT_MS: '60000' }),
    ).toThrow(/must exceed/u);
  });

  it('requires an endpoint and complete identity for external OCR', () => {
    expect(() => load({ VISION_PROVIDER_MODE: 'azure' })).toThrow(/require/u);
    expect(() =>
      load({
        VISION_PROVIDER_MODE: 'azure',
        VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT: 'https://vision.example.com',
        AZURE_TENANT_ID: 'tenant',
        AZURE_CLIENT_ID: 'client',
      }),
    ).toThrow(/AZURE_CLIENT_SECRET/u);
  });

  it('forwards only the declared external-provider identity', () => {
    const config = load({
      VISION_PROVIDER_MODE: 'auto',
      VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT: 'https://vision.example.com',
      AZURE_TENANT_ID: 'tenant',
      AZURE_CLIENT_ID: 'client',
      AZURE_CLIENT_SECRET: 'secret',
    });
    expect(config.vision.workerEnvironment).toMatchObject({
      VISION_PROVIDER_MODE: 'auto',
      VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT: 'https://vision.example.com',
      AZURE_TENANT_ID: 'tenant',
      AZURE_CLIENT_ID: 'client',
      AZURE_CLIENT_SECRET: 'secret',
    });
  });

  it('requires complete Azure storage settings', () => {
    expect(() => load({ VISION_STORAGE_BACKEND: 'azure_blob' })).toThrow(/requires/u);
  });
});
