#!/usr/bin/env node
import { startStdioAgentToolApplication } from '@agent-tool-platform/runtime/capability';
import { capability } from './capability.js';

const stdio = await startStdioAgentToolApplication(capability);

await new Promise<void>((resolve, reject) => {
  let closing = false;
  const close = (): void => {
    if (closing) return;
    closing = true;
    process.stdin.off('end', close);
    process.stdin.off('close', close);
    void stdio.close().then(resolve, reject);
  };

  process.stdin.once('end', close);
  process.stdin.once('close', close);
  if (process.stdin.readableEnded || process.stdin.destroyed) close();
});
