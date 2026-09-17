# Contributing

Keep the TypeScript layer thin and Platform-facing. Tool schemas, routing, configuration, readiness,
and worker result translation belong there. Image decoding, OCR, provider normalization, comparison,
optimization, and visual evidence interpretation remain in Python.

Do not add a second MCP/HTTP server, generic process supervisor, agent host, registry, or deployment
framework. Reuse Agent Tool Platform mechanics. Do not expose arbitrary commands or forward the
parent environment to the worker.

Before submitting a change, run the smallest affected tests and then the consolidated gates:

```bash
npm ci
npm run format:check
npm run lint
npm run typecheck
npm run test:coverage
npm run build
npm run metadata:validate
npm run package:smoke

python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
mypy
pytest
```

Provider tests must use fakes; CI must not need provider credentials, network inference, model
weights, or a live Azure resource. New image formats require explicit magic/structure, byte, and
decoded-work bounds. New interpretation rules must be generic and tested against more than one
value; never encode benchmark fixture answers in production code.
