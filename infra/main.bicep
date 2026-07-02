targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the environment — used to name the resource group and derive a unique token.')
param environmentName string

@minLength(1)
@description('Primary Azure region for all resources.')
param location string

@description('Azure Voice Live endpoint (https://<resource>.services.ai.azure.com).')
param voiceLiveEndpoint string = ''

@description('Voice Live model deployment name.')
param voiceLiveModel string = 'gpt-4.1-mini'

@description('BYOM profile (empty for managed default).')
param byomProfile string = ''

@description('Foundry resource override for BYOM (empty for managed default).')
param foundryResourceOverride string = ''

@description('Email address the app escalates calls to.')
param escalationEmail string = ''

@description('ACS verified sender address for escalation emails (e.g. DoNotReply@<guid>.azurecomm.net).')
param acsEmailSender string = ''

@description('ACS connection string used to send escalation emails. Stored as a Container App secret.')
@secure()
param acsEmailConnectionString string = ''

var tags = { 'azd-env-name': environmentName }
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    voiceLiveEndpoint: voiceLiveEndpoint
    voiceLiveModel: voiceLiveModel
    byomProfile: byomProfile
    foundryResourceOverride: foundryResourceOverride
    escalationEmail: escalationEmail
    acsEmailSender: acsEmailSender
    acsEmailConnectionString: acsEmailConnectionString
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.AZURE_CONTAINER_REGISTRY_ENDPOINT
output AZURE_RESOURCE_GROUP string = rg.name
output WEB_URI string = resources.outputs.WEB_URI
output WEB_IDENTITY_CLIENT_ID string = resources.outputs.WEB_IDENTITY_CLIENT_ID
output WEB_IDENTITY_PRINCIPAL_ID string = resources.outputs.WEB_IDENTITY_PRINCIPAL_ID
