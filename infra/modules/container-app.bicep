param location string
param environmentName string
param appName string
param containerImage string
param registryServer string
param identityId string
param identityClientId string

@description('Key Vault secret URI holding the API keys. Key Vault is used only for this unavoidable secret.')
param apiKeySecretUri string

param logAnalyticsCustomerId string
@secure()
param logAnalyticsSharedKey string
@secure()
param applicationInsightsConnectionString string

@description('Blob endpoint of the private asset storage account.')
param storageAccountUrl string
param inputContainerName string
param artifactContainerName string

@description('Content Understanding endpoint; empty deploys the server in local-only provider mode.')
param contentUnderstandingEndpoint string

@description('OCR provider mode: local, azure, or auto.')
@allowed([
  'local'
  'azure'
  'auto'
])
param providerMode string

@description('Asset time to live in seconds. Must match the storage lifecycle deletion rule.')
param assetTtlSeconds int

@description('Per-operation timeout in seconds.')
param operationTimeoutSeconds int

@description('vCPU per replica.')
param cpu string

@description('Memory per replica, for example 4Gi.')
param memory string

param minReplicas int
param maxReplicas int

@description('Concurrent HTTP requests per replica before scaling out.')
param httpConcurrency int

param tags object

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        allowInsecure: false
        targetPort: 8080
        transport: 'auto'
      }
      registries: [
        {
          server: registryServer
          identity: identityId
        }
      ]
      secrets: [
        {
          name: 'api-key'
          keyVaultUrl: apiKeySecretUri
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'vision-server'
          image: containerImage
          resources: {
            cpu: json(cpu)
            memory: memory
          }
          env: [
            {
              name: 'VISION_ENVIRONMENT'
              value: 'production'
            }
            {
              name: 'VISION_AUTH_ENABLED'
              value: 'true'
            }
            {
              name: 'VISION_API_KEYS'
              secretRef: 'api-key'
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identityClientId
            }
            {
              name: 'VISION_ASSET_BACKEND'
              value: 'blob'
            }
            {
              name: 'VISION_AZURE_STORAGE_ACCOUNT_URL'
              value: storageAccountUrl
            }
            {
              name: 'VISION_AZURE_ASSET_CONTAINER'
              value: inputContainerName
            }
            {
              name: 'VISION_AZURE_ARTIFACT_CONTAINER'
              value: artifactContainerName
            }
            {
              name: 'VISION_ASSET_TTL_SECONDS'
              value: string(assetTtlSeconds)
            }
            {
              name: 'VISION_PROVIDER_MODE'
              value: providerMode
            }
            {
              name: 'VISION_AZURE_CONTENT_UNDERSTANDING_ENDPOINT'
              value: contentUnderstandingEndpoint
            }
            {
              name: 'VISION_OPERATION_TIMEOUT_SECONDS'
              value: string(operationTimeoutSeconds)
            }
            {
              name: 'VISION_MAX_CONCURRENCY'
              value: string(httpConcurrency)
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: applicationInsightsConnectionString
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8080
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/ready'
                port: 8080
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: string(httpConcurrency)
              }
            }
          }
        ]
      }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
