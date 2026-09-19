import { readFile } from 'node:fs/promises';
import { describe, expect, it } from 'vitest';

const load = (path: string): Promise<string> => readFile(new URL(path, import.meta.url), 'utf8');

describe('D4 workflow callers', () => {
  it('publishes the canonical pinned release caller without an npm token', async () => {
    const workflow = await load('../../.github/workflows/release.yml');

    expect(workflow).toContain("      - 'v*'");
    expect(workflow).toContain('  workflow_dispatch:');
    expect(workflow).toContain('      version:');
    expect(workflow).toContain('      dry_run:');
    expect(workflow).toContain('      recover_github_release:');
    expect(workflow).toContain('  contents: write');
    expect(workflow).toContain('  id-token: write');
    expect(workflow).toContain(
      'uses: ashergarland/agent-tool-platform/.github/workflows/capability-release.yml@98ec8162fb11d5c04aee9e6f7b3625a472a0180d',
    );
    expect(workflow).toContain("version: ${{ inputs.version || '' }}");
    expect(workflow).toContain(
      "dry_run: ${{ github.event_name == 'workflow_dispatch' && inputs.dry_run || false }}",
    );
    expect(workflow).toContain(
      "recover_github_release: ${{ github.event_name == 'workflow_dispatch' && inputs.recover_github_release || false }}",
    );
    expect(workflow).not.toMatch(/npm[_-]?token|secrets\./iu);
  });

  it('keeps CI and security callers pinned and narrows Python audit permissions', async () => {
    const [ci, security] = await Promise.all([
      load('../../.github/workflows/ci.yml'),
      load('../../.github/workflows/security.yml'),
    ]);
    const pin = '@98ec8162fb11d5c04aee9e6f7b3625a472a0180d';

    expect(ci).toContain(`capability-ci.yml${pin}`);
    expect(security).toContain(`capability-security.yml${pin}`);
    expect(security).toMatch(
      /python-dependency-audit:\s+name: Python dependency audit\s+runs-on: ubuntu-latest\s+permissions:\s+contents: read/u,
    );
    expect(security).toMatch(
      /python-codeql:\s+name: Python CodeQL[\s\S]*?permissions:\s+contents: read\s+security-events: write\s+packages: read/u,
    );
  });
});
