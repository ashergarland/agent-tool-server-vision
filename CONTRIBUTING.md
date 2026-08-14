# Contributing

Use Python 3.11 or newer and install development dependencies with `pip install -e '.[dev]'`.

Keep transport concerns in the FastAPI application, image processing in services, and model-specific
logic behind the `OcrEngine` protocol. Tests must not download model weights; inject a deterministic
fake engine instead.

Before opening a pull request, run the complete validation list in `README.md`. Never commit `.env`
files, model outputs containing user data, credentials, tenant/subscription identifiers, or generated
secrets.
