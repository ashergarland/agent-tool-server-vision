import { readFile } from 'node:fs/promises';
import { describe, expect, it } from 'vitest';
import { capability } from '../../src/capability.js';

const load = async (path: string): Promise<unknown> =>
  JSON.parse(await readFile(new URL(path, import.meta.url), 'utf8'));

describe('Vision D4/v1 profile truthfulness', () => {
  it('matches repository, package, entrypoint, and schema identity', async () => {
    const declaration = (await load('../../capability-profiles.json')) as {
      capability: { id: string; displayName: string; repository: string };
      profiles: {
        id: string;
        configuration: { schema: { id: string; capabilityId: string; path: string } };
        delivery: { publication: { identifier: string }; entrypoint: unknown };
      }[];
    };
    const server = (await load('../../server.json')) as {
      name: string;
      repository: { url: string };
    };
    const manifest = (await load('../../package.json')) as { name: string; version: string };

    expect(declaration.capability).toEqual({
      id: server.name,
      displayName: capability.manifest.title,
      repository: server.repository.url,
    });
    expect(capability.manifest.version).toBe(manifest.version);
    for (const profile of declaration.profiles) {
      expect(profile.delivery.publication.identifier).toBe(manifest.name);
      expect(profile.delivery.entrypoint).toEqual({
        reference: 'dist/stdio.js',
        interface: 'stdio',
      });
      const schema = (await load(`../../${profile.configuration.schema.path}`)) as {
        $id: string;
      };
      expect(profile.configuration.schema).toEqual({
        id: schema.$id,
        capabilityId: declaration.capability.id,
        path: profile.configuration.schema.path,
      });
    }
  });

  it('declares separate local and hybrid package profiles with gated artifacts', async () => {
    const declaration = (await load('../../capability-profiles.json')) as {
      profiles: {
        id: string;
        dimensions: Record<string, string>;
        requiredSecrets: string[];
        providerPrerequisites: unknown[];
        mutation?: unknown;
      }[];
    };
    const local = declaration.profiles.find((profile) => profile.id === 'local-package');
    const hybrid = declaration.profiles.find((profile) => profile.id === 'hybrid-azure-package');
    expect(local?.dimensions).toEqual({
      execution: 'local',
      delivery: 'package',
      access: 'local-process',
      workload: 'filesystem',
      provider: 'none',
      mutation: 'mutating',
    });
    expect(local?.requiredSecrets).toEqual([]);
    expect(local?.providerPrerequisites).toEqual([]);
    expect(hybrid?.dimensions.provider).toBe('external');
    expect(hybrid?.requiredSecrets).toEqual(['AZURE_CLIENT_SECRET']);
    expect(hybrid?.providerPrerequisites).toHaveLength(1);
    expect(hybrid?.dimensions.mutation).toBe('mutating');
    expect(declaration.profiles.every((profile) => profile.mutation !== undefined)).toBe(true);
  });

  it('contains no hosted or operator-instance claim', async () => {
    const declaration = await load('../../capability-profiles.json');
    const serialized = JSON.stringify(declaration);
    expect(serialized).not.toMatch(/"execution":"hosted"|container-app|subscriptionId|tenantId/iu);
    expect(serialized).not.toContain('secretValue');
  });

  it('publishes every bounded package setting and fixes the hybrid provider', async () => {
    const local = (await load('../../schemas/local-configuration.schema.json')) as {
      properties: Record<string, unknown>;
    };
    const hybrid = (await load('../../schemas/hybrid-azure-configuration.schema.json')) as {
      properties: Record<string, { const?: string }>;
    };
    const common = [
      'VISION_ALLOWED_ROOTS',
      'VISION_PYTHON_PATH',
      'VISION_PROVIDER_MODE',
      'VISION_DEFAULT_LANGUAGE',
      'VISION_PADDLE_LANGUAGES',
      'VISION_PADDLE_CACHE_SIZE',
      'VISION_MAX_IMAGE_BYTES',
      'VISION_MAX_IMAGE_PIXELS',
      'VISION_STORAGE_BACKEND',
      'VISION_ASSET_ROOT',
      'VISION_ASSET_TTL_SECONDS',
      'VISION_ASSET_MAX_BYTES',
      'VISION_ASSET_QUOTA_BYTES',
      'VISION_ASSET_QUOTA_COUNT',
      'VISION_MAX_CONCURRENCY',
      'VISION_MAX_QUEUE_DEPTH',
      'VISION_OPERATION_TIMEOUT_SECONDS',
      'VISION_PROVIDER_TIMEOUT_SECONDS',
      'VISION_SHUTDOWN_GRACE_SECONDS',
      'VISION_WORKER_TIMEOUT_MS',
      'VISION_WORKER_MAX_OUTPUT_BYTES',
    ];
    expect(Object.keys(local.properties)).toEqual(expect.arrayContaining(common));
    expect(Object.keys(hybrid.properties)).toEqual(expect.arrayContaining(common));
    expect(hybrid.properties['VISION_PROVIDER_MODE']?.const).toBe('azure');
  });
});
