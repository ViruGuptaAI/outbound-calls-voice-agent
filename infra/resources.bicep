@description('Azure region for all resources.')
param location string

@description('Tags applied to all resources.')
param tags object

@description('Unique token to suffix resource names.')
param resourceToken string

param voiceLiveEndpoint string
param voiceLiveModel string
param byomProfile string
param foundryResourceOverride string
param escalationEmail string

var prefix = 'oc'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-log-${resourceToken}'
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env-${resourceToken}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: '${prefix}acr${resourceToken}'
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-id-${resourceToken}'
  location: location
  tags: tags
}

// AcrPull so the app identity can pull the image from ACR
var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, uami.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-web-${resourceToken}'
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: uami.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          // Placeholder image for first provision; azd deploy pushes the real image.
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: [
            {
              name: 'APP_PORT'
              value: '8000'
            }
            {
              name: 'AZURE_VOICE_LIVE_ENDPOINT'
              value: voiceLiveEndpoint
            }
            {
              name: 'VOICE_LIVE_MODEL'
              value: voiceLiveModel
            }
            {
              name: 'BYOM_PROFILE'
              value: byomProfile
            }
            {
              name: 'FOUNDRY_RESOURCE_OVERRIDE'
              value: foundryResourceOverride
            }
            {
              name: 'ESCALATION_EMAIL'
              value: escalationEmail
            }
            {
              name: 'AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID'
              value: uami.properties.clientId
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
  dependsOn: [
    acrPull
  ]
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output WEB_URI string = 'https://${web.properties.configuration.ingress.fqdn}'
output WEB_IDENTITY_CLIENT_ID string = uami.properties.clientId
output WEB_IDENTITY_PRINCIPAL_ID string = uami.properties.principalId
