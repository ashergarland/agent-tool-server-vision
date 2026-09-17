import {
  defineAgentToolCapability,
  type AgentToolCapability,
  type CapabilityContext,
} from '@agent-tool-platform/runtime/capability';
import { visionConfig, type VisionConfig } from './config.js';
import { capabilityManifest } from './manifest.js';
import { capabilityTools } from './tools/definitions.js';
import { capabilityInstructions } from './tools/guidance.js';
import { createPythonVisionWorker } from './worker/client.js';
import type { VisionWorker } from './worker/types.js';

export interface VisionServices {
  readonly worker: VisionWorker;
}

export type VisionWorkerFactory = (
  context: CapabilityContext<VisionConfig>,
) => Promise<VisionWorker>;

export const createVisionCapability = (
  createWorker: VisionWorkerFactory = createPythonVisionWorker,
): AgentToolCapability<VisionServices, VisionConfig> =>
  defineAgentToolCapability({
    manifest: capabilityManifest,
    instructions: capabilityInstructions,
    config: visionConfig,
    tools: capabilityTools,

    async createServices(context): Promise<VisionServices> {
      return { worker: await createWorker(context) };
    },

    lifecycle: {
      async stop({ services }): Promise<void> {
        await services.worker.drain();
      },
    },

    readiness: [({ services }) => services.worker.readiness()],
  });

export const capability = createVisionCapability();
