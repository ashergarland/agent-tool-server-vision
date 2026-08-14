@description('Azure region for the storage account.')
param location string

@description('Globally unique storage account name.')
@minLength(3)
@maxLength(24)
param name string

@description('Container holding uploaded input images.')
param inputContainerName string = 'vision-input'

@description('Container holding generated artifacts such as crops and diffs.')
param artifactContainerName string = 'vision-artifacts'

@description('Asset lifetime in days. Blob lifecycle deletion must match the application TTL.')
@minValue(1)
param assetRetentionDays int

@description('Principal ID of the managed identity that reads and writes blobs.')
param dataPrincipalId string

param tags object

var blobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: false
    }
  }
}

resource inputContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: inputContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource artifactContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: artifactContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'expire-assets'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [
                'blockBlob'
              ]
              prefixMatch: [
                inputContainerName
                artifactContainerName
              ]
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterCreationGreaterThan: assetRetentionDays
                }
              }
            }
          }
        }
      ]
    }
  }
  dependsOn: [
    inputContainer
    artifactContainer
  ]
}

resource blobDataAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, dataPrincipalId, blobDataContributorRoleId)
  properties: {
    principalId: dataPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      blobDataContributorRoleId
    )
  }
}

output name string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
output inputContainerName string = inputContainer.name
output artifactContainerName string = artifactContainer.name
