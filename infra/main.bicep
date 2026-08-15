targetScope = 'subscription'

@description('Short environment suffix such as dev, test, or prod.')
@minLength(2)
@maxLength(12)
param environmentName string

@description('Azure region for all resources. Defaults to the deployment location so no region is hard coded.')
param location string = deployment().location

@description('Immutable container image reference used on the second pass.')
param containerImage string = 'replace.invalid/agent-tool-server-vision:replace-me'

@description('False for the prerequisite pass; true only after the Key Vault secret and image exist.')
param deployApp bool = false

@description('Existing Key Vault secret name used by the application.')
param apiKeySecretName string = 'tool-server-api-key'

@description('Object ID allowed to seed the Key Vault secret during bootstrap; leave blank outside bootstrap.')
param bootstrapPrincipalObjectId string = ''

@description('Deploy an Azure AI Services account for Content Understanding.')
param deployContentUnderstanding bool = true

@description('OCR provider mode used by the hosted server.')
@allowed([
  'local'
  'azure'
  'auto'
])
param providerMode string = 'azure'

@description('Asset time to live in seconds; the blob lifecycle rule deletes on the matching day boundary.')
@minValue(3600)
param assetTtlSeconds int = 86400

@description('Per-operation timeout in seconds.')
@minValue(5)
param operationTimeoutSeconds int = 60

@description('vCPU per replica.')
param cpu string = '2'

@description('Memory per replica.')
param memory string = '4Gi'

@description('Minimum replicas. Keep zero for scale-to-zero.')
@minValue(0)
param minReplicas int = 0

@description('Maximum replicas.')
@minValue(1)
param maxReplicas int = 5

@description('Concurrent HTTP requests per replica before scaling out.')
@minValue(1)
param httpConcurrency int = 10

var suffix = uniqueString(subscription().id, environmentName)
var resourceGroupName = 'rg-atsv-${environmentName}-${suffix}'
var assetRetentionDays = max(1, assetTtlSeconds / 86400)

resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: {
    application: 'agent-tool-server-vision'
    environment: environmentName
    managedBy: 'bicep'
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  scope: resourceGroup
  params: {
    location: location
    name: 'id-atsv-${environmentName}-${suffix}'
    tags: resourceGroup.tags
  }
}

module registry 'modules/container-registry.bicep' = {
  name: 'registry'
  scope: resourceGroup
  params: {
    location: location
    name: 'cratsv${suffix}'
    pullPrincipalId: identity.outputs.principalId
    tags: resourceGroup.tags
  }
}

module keyVault 'modules/key-vault.bicep' = {
  name: 'key-vault'
  scope: resourceGroup
  params: {
    location: location
    name: 'kv-atsv-${suffix}'
    accessPrincipalObjectId: identity.outputs.principalId
    bootstrapPrincipalObjectId: bootstrapPrincipalObjectId
    tags: resourceGroup.tags
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: resourceGroup
  params: {
    location: location
    name: 'statsv${suffix}'
    assetRetentionDays: assetRetentionDays
    dataPrincipalId: identity.outputs.principalId
    tags: resourceGroup.tags
  }
}

module contentUnderstanding 'modules/content-understanding.bicep' = if (deployContentUnderstanding) {
  name: 'content-understanding'
  scope: resourceGroup
  params: {
    location: location
    name: 'cu-atsv-${environmentName}-${suffix}'
    callerPrincipalId: identity.outputs.principalId
    tags: resourceGroup.tags
  }
}

module observability 'modules/observability.bicep' = {
  name: 'observability'
  scope: resourceGroup
  params: {
    location: location
    workspaceName: 'log-atsv-${environmentName}-${suffix}'
    insightsName: 'appi-atsv-${environmentName}-${suffix}'
    tags: resourceGroup.tags
  }
}

module app 'modules/container-app.bicep' = if (deployApp) {
  name: 'container-app'
  scope: resourceGroup
  params: {
    location: location
    environmentName: 'cae-atsv-${environmentName}-${suffix}'
    appName: 'ca-atsv-${environmentName}-${suffix}'
    containerImage: containerImage
    registryServer: registry.outputs.loginServer
    identityId: identity.outputs.id
    identityClientId: identity.outputs.clientId
    apiKeySecretUri: '${keyVault.outputs.vaultUri}secrets/${apiKeySecretName}'
    logAnalyticsCustomerId: observability.outputs.workspaceCustomerId
    logAnalyticsSharedKey: observability.outputs.workspaceSharedKey
    applicationInsightsConnectionString: observability.outputs.applicationInsightsConnectionString
    storageAccountUrl: storage.outputs.blobEndpoint
    inputContainerName: storage.outputs.inputContainerName
    artifactContainerName: storage.outputs.artifactContainerName
    contentUnderstandingEndpoint: deployContentUnderstanding ? contentUnderstanding!.outputs.endpoint : ''
    providerMode: providerMode
    assetTtlSeconds: assetTtlSeconds
    operationTimeoutSeconds: operationTimeoutSeconds
    cpu: cpu
    memory: memory
    minReplicas: minReplicas
    maxReplicas: maxReplicas
    httpConcurrency: httpConcurrency
    tags: resourceGroup.tags
  }
}

output resourceGroupName string = resourceGroupName
output registryName string = registry.outputs.name
output registryLoginServer string = registry.outputs.loginServer
output keyVaultName string = keyVault.outputs.name
output managedIdentityClientId string = identity.outputs.clientId
output storageAccountName string = storage.outputs.name
output contentUnderstandingEndpoint string = deployContentUnderstanding ? contentUnderstanding!.outputs.endpoint : ''
output applicationUrl string = deployApp ? 'https://${app!.outputs.fqdn}' : ''
