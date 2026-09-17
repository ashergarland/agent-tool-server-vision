import type { CapabilityManifest } from '@agent-tool-platform/runtime/capability';
import packageManifest from '../package.json' with { type: 'json' };

export const capabilityManifest: CapabilityManifest = {
  name: 'agent-tool-server-vision',
  version: packageManifest.version,
  title: 'Vision',
  description:
    'Bounded image, OCR, comparison, optimization, and structured visual analysis backed by a capability-owned Python worker.',
  documentationUrl: 'https://github.com/ashergarland/agent-tool-server-vision#readme',
};
