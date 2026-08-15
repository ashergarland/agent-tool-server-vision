# Contributing

Use Python 3.11 or newer and install development dependencies with `pip install -e '.[dev]'`.

Declare tools only in `src/vision_server/registry.py`; transports must derive names, descriptions,
schemas, and routes from it. Keep provider-specific logic behind the `OcrProvider` protocol, image
and path security in `imaging.py`, and storage behind `AssetStore`. Tests must not require Azure,
network access, or model weights; inject the deterministic fakes in `tests/conftest.py` instead.

Before opening a pull request, run the complete validation list in `README.md`. Never commit `.env`
files, model outputs containing user data, credentials, tenant/subscription identifiers, or generated
secrets.
