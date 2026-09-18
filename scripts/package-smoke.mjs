import { spawn, spawnSync } from 'node:child_process';
import { constants as fsConstants } from 'node:fs';
import { access, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const repositoryRoot = fileURLToPath(new URL('../', import.meta.url));
const npmCli = process.env['npm_execpath'];

const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};

const runNode = (args, options = {}) => {
  const result = spawnSync(process.execPath, args, {
    cwd: repositoryRoot,
    env: { ...process.env, npm_config_update_notifier: 'false' },
    encoding: 'utf8',
    windowsHide: true,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(
      `${process.execPath} ${args.join(' ')} failed:\n${result.stdout}${result.stderr}`,
    );
  }
  return result.stdout;
};

const runNpm = (args, options = {}) => {
  assert(npmCli, 'package:smoke must be invoked through npm');
  return runNode([npmCli, ...args], options);
};

class McpClient {
  constructor(entrypoint, cwd, processTemp) {
    this.buffer = '';
    this.nextId = 1;
    this.pending = new Map();
    this.nonProtocolOutput = [];
    this.stderr = '';
    this.child = spawn(process.execPath, [entrypoint], {
      cwd,
      env: {
        ...process.env,
        USERPROFILE: processTemp,
        TEMP: processTemp,
        TMP: processTemp,
        TMPDIR: processTemp,
        VISION_ALLOWED_ROOTS: cwd,
        VISION_PROVIDER_MODE: 'local',
      },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    this.child.stdout.setEncoding('utf8');
    this.child.stderr.setEncoding('utf8');
    this.child.stdout.on('data', (chunk) => this.consume(chunk));
    this.child.stderr.on('data', (chunk) => {
      this.stderr += chunk;
    });
    this.exit = new Promise((resolve) => {
      this.child.once('exit', (code, signal) => resolve({ code, signal }));
    });
  }

  consume(chunk) {
    this.buffer += chunk;
    let newline = this.buffer.indexOf('\n');
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (line) this.dispatch(line);
      newline = this.buffer.indexOf('\n');
    }
  }

  dispatch(line) {
    let response;
    try {
      response = JSON.parse(line);
    } catch {
      this.nonProtocolOutput.push(line);
      return;
    }
    if (typeof response.id !== 'number') return;
    const pending = this.pending.get(response.id);
    if (!pending) return;
    this.pending.delete(response.id);
    clearTimeout(pending.timer);
    pending.resolve(response);
  }

  request(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`MCP request timed out: ${method}\n${this.stderr}`));
      }, 30_000);
      timer.unref?.();
      this.pending.set(id, { resolve, timer });
      this.child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
    });
  }

  notify(method, params = {}) {
    this.child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', method, params })}\n`);
  }

  async initialize() {
    const response = await this.request('initialize', {
      protocolVersion: '2025-06-18',
      capabilities: {},
      clientInfo: { name: 'vision-package-smoke', version: '1.0.0' },
    });
    this.notify('notifications/initialized');
    return response;
  }

  async shutdown() {
    this.child.stdin.end();
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Packed stdio entrypoint did not exit')), 15_000);
      timer.unref?.();
    });
    try {
      return await Promise.race([this.exit, timeout]);
    } finally {
      clearTimeout(timer);
    }
  }

  terminate() {
    if (this.child.exitCode === null && this.child.signalCode === null) {
      this.child.kill('SIGKILL');
    }
  }
}

const main = async () => {
  const manifest = JSON.parse(await readFile(join(repositoryRoot, 'package.json'), 'utf8'));
  const packageName = manifest.name;
  const bins = Object.entries(manifest.bin ?? {});
  assert(typeof packageName === 'string' && packageName.length > 0, 'Package name is missing');
  assert(bins.length === 1, 'Package smoke expects exactly one public executable');
  const [binName, binTarget] = bins[0];

  const temporaryRoot = await mkdtemp(join(tmpdir(), 'vision-package-smoke-'));
  let client;
  try {
    const outside = relative(repositoryRoot, temporaryRoot);
    assert(
      outside.startsWith('..') || isAbsolute(outside),
      'The package consumer must be outside the source checkout',
    );

    runNpm(['run', 'build']);
    const packDirectory = join(temporaryRoot, 'pack');
    await mkdir(packDirectory);
    const packed = JSON.parse(
      runNpm(['pack', '--ignore-scripts', '--json', '--pack-destination', packDirectory]),
    );
    assert(Array.isArray(packed) && packed.length === 1, 'npm pack returned no single candidate');
    const candidate = packed[0];
    assert(candidate.name === packageName, `npm pack selected ${String(candidate.name)}`);

    const packedFiles = candidate.files.map((file) => file.path);
    for (const required of [
      'LICENSE',
      'README.md',
      'dist/public.d.ts',
      'dist/public.js',
      binTarget,
      'package.json',
      'pyproject.toml',
      'src/vision_server/worker.py',
      'src/vision_server/svg_analysis.py',
    ]) {
      assert(packedFiles.includes(required), `Packed package is missing ${required}`);
    }
    for (const forbidden of ['tests/', 'scripts/', '.github/', 'coverage/', 'infra/']) {
      assert(
        !packedFiles.some((path) => path.startsWith(forbidden)),
        `Packed package contains ${forbidden}`,
      );
    }
    assert(
      !packedFiles.some((path) => path.includes('/__pycache__/') || /\.py[co]$/u.test(path)),
      'Packed package contains generated Python bytecode',
    );

    const tarball = join(packDirectory, candidate.filename);
    const consumer = join(temporaryRoot, 'consumer');
    await mkdir(consumer);
    await writeFile(
      join(consumer, 'package.json'),
      `${JSON.stringify(
        { name: 'vision-package-smoke-consumer', private: true, type: 'module' },
        null,
        2,
      )}\n`,
    );
    runNpm(
      ['install', '--ignore-scripts', '--no-audit', '--no-fund', '--package-lock=false', tarball],
      { cwd: consumer },
    );

    const installed = join(consumer, 'node_modules', ...packageName.split('/'));
    const installedManifest = JSON.parse(await readFile(join(installed, 'package.json'), 'utf8'));
    assert(installedManifest.name === packageName, 'External consumer installed the wrong package');
    assert(
      installedManifest.version === candidate.version,
      'Installed version differs from the packed candidate',
    );
    assert(installedManifest.bin?.[binName] === binTarget, 'Installed bin target changed');

    await writeFile(
      join(consumer, 'consumer.mjs'),
      `import { capability, capabilityManifest } from ${JSON.stringify(packageName)};\n` +
        `if (capability.manifest !== capabilityManifest) throw new Error('exports do not compose');\n` +
        `if (capability.tools.length !== 4) throw new Error('unexpected tool catalogue');\n`,
    );
    runNode(['consumer.mjs'], { cwd: consumer });

    const installedEntry = join(installed, binTarget);
    await access(installedEntry, fsConstants.R_OK);
    const installedBin = join(
      consumer,
      'node_modules',
      '.bin',
      process.platform === 'win32' ? `${binName}.cmd` : binName,
    );
    await access(installedBin, fsConstants.R_OK);
    if (process.platform !== 'win32') {
      assert(((await stat(installedBin)).mode & 0o111) !== 0, 'Installed bin is not executable');
    }

    const fixture = join(consumer, 'smoke.svg');
    const processTemp = join(consumer, 'process-temp');
    await mkdir(processTemp);
    await writeFile(
      fixture,
      '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">' +
        '<text x="10" y="30">Revision state</text><text x="10" y="60">Degraded</text>' +
        '<text x="10" y="90">server_started address=127.0.0.1 port=4321</text></svg>',
    );

    client = new McpClient(installedEntry, consumer, processTemp);
    const initialized = await client.initialize();
    assert(initialized.result?.serverInfo, 'Packed entrypoint returned no MCP server identity');
    assert(
      initialized.result.serverInfo.version === candidate.version,
      `Packed entrypoint reported ${String(
        initialized.result.serverInfo.version,
      )}, expected ${candidate.version}`,
    );
    const liveWorkspaces = (await readdir(processTemp)).filter((name) =>
      name.startsWith('vision-worker-'),
    );
    assert(
      liveWorkspaces.length === 1,
      `Packed entrypoint created ${liveWorkspaces.length} Vision scratch workspaces`,
    );
    assert(
      (await stat(join(processTemp, liveWorkspaces[0]))).isDirectory(),
      'Packed entrypoint scratch workspace is not a directory',
    );

    const listed = await client.request('tools/list');
    const names = (listed.result?.tools ?? []).map((tool) => tool.name);
    assert(
      JSON.stringify(names) ===
        JSON.stringify([
          'analyze_image',
          'extract_text_and_layout',
          'compare_images',
          'optimize_image_region',
        ]),
      `Packed entrypoint exposed unexpected tools: ${names.join(', ')}`,
    );

    const invocation = await client.request('tools/call', {
      name: 'analyze_image',
      arguments: { image: { kind: 'local_path', path: fixture } },
    });
    assert(invocation.result?.isError !== true, 'analyze_image failed from the packed entrypoint');
    const result = invocation.result?.structuredContent;
    assert(
      result?.facts?.some((fact) => fact.kind === 'listener' && fact.value === '4321'),
      'Packed worker did not return listener evidence',
    );
    assert(result?.fallbackUsed === false, 'Packed worker unexpectedly used fallback');

    const exit = await client.shutdown();
    assert(exit.code === 0, `Packed entrypoint exited with code ${String(exit.code)}`);
    assert(client.nonProtocolOutput.length === 0, 'Packed entrypoint wrote non-protocol stdout');
    assert(client.stderr === '', `Packed entrypoint wrote to stderr:\n${client.stderr}`);
    assert(
      !(await readdir(processTemp)).some((name) => name.startsWith('vision-worker-')),
      'Packed entrypoint left its Platform-owned worker workspace behind',
    );

    process.stdout.write(
      `Package smoke passed for ${packageName}@${candidate.version}: ` +
        `${packedFiles.length} files, installed and invoked outside the repository.\n`,
    );
  } finally {
    client?.terminate();
    await rm(temporaryRoot, { recursive: true, force: true, maxRetries: 3 });
  }
};

await main();
