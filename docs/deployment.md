# Azure deployment

The infrastructure in `infra/` is portable: it contains no subscription, tenant, resource group,
principal, service name, endpoint, region, or billing value. Anyone can fork this repository and
deploy it into their own tenant. Local-only mode requires no Azure at all.

## What is provisioned

| Resource | Purpose |
| -------- | ------- |
| User-assigned managed identity | Registry pull, Key Vault read, Blob data access, Content Understanding inference |
| Container Registry | Application image |
| Key Vault | Only the unavoidable API-key secret |
| Storage account | Two private containers (`vision-input`, `vision-artifacts`) with a lifecycle rule that deletes blobs on the TTL day boundary |
| Azure AI Services account (optional) | Content Understanding read/layout, `disableLocalAuth`, Entra only |
| Log Analytics and Application Insights | Operational telemetry |
| Container Apps environment and app | The server itself |

No Azure Machine Learning resource and no Image Analysis 4.0 resource is provisioned.

## Defaults

2 vCPU, 4 GiB memory, `minReplicas` 0 (scale to zero), `maxReplicas` 5, HTTP concurrency 10,
`/health` for liveness and `/ready` for readiness. Location, naming, CPU, memory, replicas,
concurrency, storage, provider mode, TTL, and timeouts are all parameters.

## Prerequisites

- Azure CLI with the Bicep CLI
- Docker
- permission to create subscription deployments, a resource group, and role assignments
- a selected subscription (`az account set --subscription <your-subscription>`)

Never commit subscription IDs, tenant IDs, generated resource names, or credentials.

## Two-pass provisioning

```bash
# choose your own environment name and region
./scripts/bootstrap/provision.sh dev <your-region>
```

The script deploys shared resources with `deployApp=false`, writes a generated API key straight to
Key Vault, builds and pushes the image, then deploys again with `deployApp=true`. The first pass
prevents Container Apps from starting before its Key Vault secret exists.

Hosted deployment defaults to managed OCR (`deployContentUnderstanding=true` and
`providerMode=azure`); the endpoint is wired into the app automatically. Check regional
availability of Content Understanding before choosing a region. For a local-only deployment, set
`deployContentUnderstanding=false`, set `providerMode=local`, and build the image with
`--build-arg EXTRAS=ml,azure`.

## Identity and secrets

The Container App uses its user-assigned managed identity for every Azure call: ACR pull, the Key
Vault secret reference, Blob data access (Storage Blob Data Contributor, scoped to the storage
account) and Content Understanding inference (Cognitive Services User, scoped to that account).
Shared key access on storage and local auth on the AI Services account are disabled, so no
connection string, account key, or SAS is ever created.

Rotate the API key by adding the replacement to the Key Vault secret, restarting revisions, moving
clients, then removing the old key.

## Per-fork GitHub OIDC setup

Deployment workflows must use federated credentials; do not create long-lived secrets.

1. In your own tenant, create (or reuse) an app registration or user-assigned managed identity.
2. Add a federated credential for your fork, for example subject
   `repo:<your-org>/<your-fork>:ref:refs/heads/main` and, if you deploy from pull requests,
   `repo:<your-org>/<your-fork>:pull_request`, with issuer `https://token.actions.githubusercontent.com`.
3. Grant that principal the roles it needs at your chosen scope (for example Contributor plus
   User Access Administrator on the target resource group, which is required for role assignments).
4. Store `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` as repository variables or
   secrets in your fork, and give the deployment job `permissions: id-token: write`.
5. Use `azure/login@v2` with those values; no client secret is required.

## Cost and cold starts

With `minReplicas: 0` the app costs nothing while idle, and the first request after idling pays a
container cold start. Enabling the local OCR provider additionally pays a one-time model load, so
prefer the managed provider or `minReplicas: 1` for latency-sensitive workloads. Content
Understanding is billed per analyzed page, storage is billed for the retained assets only (TTL
deletes them), and Log Analytics is billed by ingestion.

## Teardown

Delete the generated resource group after confirming it holds no shared resources.
