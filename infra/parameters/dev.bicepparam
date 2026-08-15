using '../main.bicep'

// Replace placeholders with your own values. No subscription, tenant, region, or
// account specific values are committed to this repository.
param environmentName = 'dev'
param deployApp = false
param containerImage = 'replace.invalid/agent-tool-server-vision:replace-me'
param deployContentUnderstanding = true
param providerMode = 'azure'
param assetTtlSeconds = 86400
param cpu = '2'
param memory = '4Gi'
param minReplicas = 0
param maxReplicas = 5
param httpConcurrency = 10
