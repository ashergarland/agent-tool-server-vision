import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createAgentToolApplication } from '@agent-tool-platform/runtime/capability';
import { createSilentLogger } from '@agent-tool-platform/runtime/logging';
import { afterEach, describe, expect, it } from 'vitest';
import { capability } from '../../src/capability.js';

const fixture = fileURLToPath(new URL('../fixtures/portal-snapshot.svg', import.meta.url));
const fixtureRoot = fileURLToPath(new URL('../fixtures', import.meta.url));
const applications: Awaited<ReturnType<typeof createAgentToolApplication>>[] = [];
const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(applications.splice(0).map((application) => application.shutdown()));
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true })),
  );
});

describe('TypeScript to Python worker integration', () => {
  it('extracts Level 2 visual facts with provenance and no provider access', async () => {
    const application = await createAgentToolApplication(capability, {
      logger: createSilentLogger(),
      env: {
        NODE_ENV: 'test',
        AUTH_MODE: 'disabled',
        VISION_ALLOWED_ROOTS: fixtureRoot,
        VISION_PROVIDER_MODE: 'local',
      },
    });
    applications.push(application);
    await application.start();

    const result = (await application.invoker.invoke({
      toolName: 'analyze_image',
      input: { image: { kind: 'local_path', path: fixture } },
      requestId: 'benchmark-roundtrip',
      principal: { id: 'test-principal', kind: 'anonymous' },
      transport: 'mcp-stdio',
    })) as {
      readonly facts: readonly {
        readonly kind: string;
        readonly value: string;
        readonly evidence: string;
        readonly confidence: number;
        readonly provenance: { readonly source: string };
      }[];
      readonly provider: { readonly name: string };
      readonly fallbackUsed: boolean;
    };

    expect(result.provider.name).toBe('embedded_svg_text');
    expect(result.fallbackUsed).toBe(false);
    expect(result.facts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: 'revision', value: 'checkout-api--pr-1842' }),
        expect.objectContaining({ kind: 'status', value: 'Degraded' }),
        expect.objectContaining({ kind: 'listener', value: '3000' }),
        expect.objectContaining({ kind: 'ingress', value: '8080' }),
        expect.objectContaining({ kind: 'readiness', value: '8080' }),
      ]),
    );
    expect(
      result.facts.every(
        (fact) =>
          fact.evidence.length > 0 &&
          fact.confidence > 0 &&
          fact.provenance.source === 'embedded_svg_text',
      ),
    ).toBe(true);
  });

  it('accepts worker facts bounded in TypeScript UTF-16 code units', async () => {
    const root = await mkdtemp(join(tmpdir(), 'vision-unicode-'));
    temporaryDirectories.push(root);
    const path = join(root, 'unicode.svg');
    const emoji = String.fromCodePoint(0x1f600);
    await writeFile(
      path,
      '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="100">' +
        `<text x="10" y="30">UNICODE_VALUE=${emoji.repeat(3000)}</text>` +
        '</svg>',
      'utf8',
    );
    const application = await createAgentToolApplication(capability, {
      logger: createSilentLogger(),
      env: {
        NODE_ENV: 'test',
        AUTH_MODE: 'disabled',
        VISION_ALLOWED_ROOTS: root,
        VISION_PROVIDER_MODE: 'local',
      },
    });
    applications.push(application);
    await application.start();

    const result = (await application.invoker.invoke({
      toolName: 'analyze_image',
      input: { image: { kind: 'local_path', path } },
      requestId: 'unicode-roundtrip',
      principal: { id: 'test-principal', kind: 'anonymous' },
      transport: 'mcp-stdio',
    })) as {
      readonly facts: readonly { readonly evidence: string }[];
      readonly meta: { readonly truncated: boolean };
    };

    expect(result.facts).toHaveLength(1);
    expect(result.facts[0]?.evidence.length).toBe(4000);
    expect(Array.from(result.facts[0]?.evidence ?? '').length).toBeLessThan(4000);
    expect(result.meta.truncated).toBe(true);
  });
});
