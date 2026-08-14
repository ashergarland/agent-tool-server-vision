@description('Azure region for the Azure AI Services (Content Understanding) account.')
param location string

@description('Globally unique account name.')
param name string

@description('Principal ID of the managed identity that calls Content Understanding.')
param callerPrincipalId string

param tags object

// Cognitive Services User: data-plane inference only, no key or management access.
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource inferenceAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, callerPrincipalId, cognitiveServicesUserRoleId)
  properties: {
    principalId: callerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      cognitiveServicesUserRoleId
    )
  }
}

output name string = account.name
output endpoint string = account.properties.endpoint
