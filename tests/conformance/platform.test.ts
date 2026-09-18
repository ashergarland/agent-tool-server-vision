import { readFile } from 'node:fs/promises';
import { basename } from 'node:path';
import type { ScratchWorkspace } from '@agent-tool-platform/runtime';
import {
  createAgentToolApplication,
  type AgentToolApplication,
} from '@agent-tool-platform/runtime/capability';
import { createSilentLogger } from '@agent-tool-platform/runtime/logging';
import { createToolRegistry } from '@agent-tool-platform/runtime/tools';
import {
  generateTestApiKey,
  runAuthConformance,
  runConfigConformance,
  runHttpConformance,
  runLifecycleConformance,
  runMcpConformance,
  runMetadataConformance,
  runOpenApiConformance,
  runProcessConformance,
  runRegistryConformance,
  runRoutingConformance,
  runScratchWorkspaceConformance,
  runTransportParity,
} from '@agent-tool-platform/testkit';
import { afterEach, describe, expect, it } from 'vitest';
import { createVisionCapability, type VisionServices } from '../../src/capability.js';
import type { VisionConfig } from '../../src/config.js';
import { capabilityManifest } from '../../src/manifest.js';
import { capabilityTools } from '../../src/tools/definitions.js';
import { capabilityInstructions } from '../../src/tools/guidance.js';
import { createPythonVisionWorker } from '../../src/worker/client.js';
import { FakeVisionWorker, sampleAnalysis } from '../helpers/fake-worker.js';

type TestApplication = AgentToolApplication<VisionConfig, VisionServices>;

const apiKey = generateTestApiKey();
const applications: TestApplication[] = [];
const readSample = {
  name: 'analyze_image',
  input: { image: { kind: 'local_path', path: '/allowed/sample.svg' } },
} as const;

const createApplication = async (start = true): Promise<TestApplication> => {
  const worker = new FakeVisionWorker();
  const application = await createAgentToolApplication(
    createVisionCapability(() => Promise.resolve(worker)),
    {
      logger: createSilentLogger(),
      env: {
        NODE_ENV: 'test',
        AUTH_MODE: 'api-key',
        API_KEYS: apiKey,
        VISION_ALLOWED_ROOTS: process.cwd(),
      },
      readinessCacheMs: 0,
    },
  );
  applications.push(application);
  if (start) await application.start();
  return application;
};

const createScratchApplication = async (): Promise<{
  application: TestApplication;
  workspace: ScratchWorkspace;
}> => {
  let workspace: ScratchWorkspace | undefined;
  const application = await createAgentToolApplication(
    createVisionCapability((context) =>
      createPythonVisionWorker({
        ...context,
        async createScratchWorkspace(options) {
          workspace = await context.createScratchWorkspace(options);
          return workspace;
        },
      }),
    ),
    {
      logger: createSilentLogger(),
      env: {
        NODE_ENV: 'test',
        AUTH_MODE: 'disabled',
        VISION_ALLOWED_ROOTS: process.cwd(),
        VISION_PYTHON_PATH: process.execPath,
      },
    },
  );
  if (workspace === undefined) {
    throw new Error('Vision worker did not request a Platform scratch workspace');
  }
  if (!basename(workspace.path).startsWith('vision-worker-')) {
    throw new Error('Vision worker requested an unexpected scratch workspace prefix');
  }
  return { application, workspace };
};

afterEach(async () => {
  await Promise.all(applications.splice(0).map((application) => application.shutdown()));
});

describe('Platform conformance', () => {
  it('satisfies registry, routing, and safe process contracts', async () => {
    const registry = createToolRegistry(capabilityTools);
    const registryResult = await runRegistryConformance({
      registry,
      services: { worker: new FakeVisionWorker() },
      invalidInputSample: {
        name: 'analyze_image',
        input: { image: { kind: 'url', url: 'https://example.invalid/image.png' } },
      },
    });
    expect(registryResult.failures).toEqual([]);
    expect(
      runRoutingConformance({ registry, instructions: capabilityInstructions }).failures,
    ).toEqual([]);
    expect((await runProcessConformance()).failures).toEqual([]);
  });

  it('satisfies authentication and configuration contracts', async () => {
    expect((await runAuthConformance()).failures).toEqual([]);
    expect(
      (
        await runConfigConformance({
          serviceName: capabilityManifest.name,
          serviceVersion: capabilityManifest.version,
        })
      ).failures,
    ).toEqual([]);
  });

  it('satisfies HTTP, MCP, OpenAPI, and transport parity contracts', async () => {
    const application = await createApplication();
    expect(
      (
        await runHttpConformance({
          app: application.http,
          registry: application.registry,
          apiKey,
          readSample: { name: readSample.name, body: readSample.input },
        })
      ).failures,
    ).toEqual([]);
    expect(
      (
        await runMcpConformance({
          createServer: () => application.createStdioServer(),
          registry: application.registry,
          instructions: capabilityInstructions,
          readSample,
        })
      ).failures,
    ).toEqual([]);
    expect(
      runOpenApiConformance({
        document: application.openApiDocument(),
        registry: application.registry,
      }).failures,
    ).toEqual([]);
    expect(
      (
        await runTransportParity({
          app: application.http,
          createMcpServer: () => application.createStdioServer(),
          apiKey,
          samples: [readSample],
        })
      ).failures,
    ).toEqual([]);
    expect(application.services.worker).toBeInstanceOf(FakeVisionWorker);
    expect(sampleAnalysis.facts).toHaveLength(1);
  });

  it('satisfies lifecycle behavior', async () => {
    expect(
      (
        await runLifecycleConformance({
          createApplication: () => createApplication(false),
        })
      ).failures,
    ).toEqual([]);
  });

  it('uses the Platform-owned scratch workspace lifecycle', async () => {
    expect(
      (
        await runScratchWorkspaceConformance({
          createApplication: createScratchApplication,
        })
      ).failures,
    ).toEqual([]);
  });

  it('publishes truthful repository metadata', async () => {
    const load = async (path: string): Promise<unknown> =>
      JSON.parse(await readFile(new URL(path, import.meta.url), 'utf8'));
    expect(
      runMetadataConformance({
        server: await load('../../server.json'),
        packageManifest: await load('../../package.json'),
        registryEntry: await load('../../examples/central-registry-entry.json'),
      }).failures,
    ).toEqual([]);
  });
});
