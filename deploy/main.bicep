// OctoClaw — Azure Container Apps deployment with Managed Identity
// Deploys: VNet, ACA Environment, Container App, Azure Files,
//          User-Assigned Managed Identity, and RBAC role assignments.
// Optionally creates Key Vault if no existing one is provided.

targetScope = 'resourceGroup'

// ── Parameters ──────────────────────────────────────────────────────────

@description('Base name for all resources')
param baseName string = 'octoclaw'

@description('Azure region')
param location string = resourceGroup().location

@description('Container image (e.g. ghcr.io/org/octoclaw:latest)')
param containerImage string

@description('GitHub token for Copilot CLI authentication')
@secure()
param githubToken string = ''

@description('CPU cores for the container app')
param cpuCores string = '1.0'

@description('Memory for the container app (e.g. 2Gi)')
param memory string = '2Gi'

@description('Minimum replicas')
param minReplicas int = 1

@description('Maximum replicas')
param maxReplicas int = 1

@description('Create a new Key Vault (false = skip KV entirely)')
param createKeyVault bool = false

@description('Pre-existing Key Vault URL (used when createKeyVault is false)')
param existingKeyVaultUrl string = ''

@description('Pre-existing Azure OpenAI / Foundry endpoint')
param azureOpenAIEndpoint string = ''

@description('Pre-existing Azure AI Search endpoint')
param azureAISearchEndpoint string = ''

@description('Pre-existing ACS connection string')
@secure()
param acsConnectionString string = ''

@description('Bot App ID (from existing Entra ID app registration)')
param botAppId string = ''

@description('Bot App Password')
@secure()
param botAppPassword string = ''

@description('Bot App Tenant ID')
param botAppTenantId string = ''

@description('ACR login server (e.g. myacr.azurecr.io)')
param acrLoginServer string = ''

@description('ACR username')
param acrUsername string = ''

@description('ACR password')
@secure()
param acrPassword string = ''

// ── Variables ───────────────────────────────────────────────────────────

var uniqueSuffix = uniqueString(resourceGroup().id, baseName)
var vnetName = '${baseName}-vnet'
var acaEnvName = '${baseName}-env'
var acaAppName = '${baseName}-app'
var identityName = '${baseName}-id'
var storageAccountName = '${take(toLower(replace(baseName, '-', '')), 11)}${take(uniqueSuffix, 8)}'
var fileShareName = 'octoclawdata'
var kvName = '${baseName}-kv-${take(uniqueSuffix, 8)}'
var logWorkspaceName = '${baseName}-logs'

// RBAC role definition IDs
var keyVaultSecretsOfficerRole = '4633458b-17de-408a-b874-0445c86b69e6'
var cognitiveServicesOpenAIUserRole = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var searchIndexDataContributorRole = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'

// ── User-Assigned Managed Identity ──────────────────────────────────────

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

// ── VNet ────────────────────────────────────────────────────────────────

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: ['10.0.0.0/16']
    }
    subnets: [
      {
        name: 'aca-subnet'
        properties: {
          addressPrefix: '10.0.0.0/23'
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

// ── Log Analytics ───────────────────────────────────────────────────────

resource logWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logWorkspaceName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ── ACA Environment ─────────────────────────────────────────────────────

resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: acaEnvName
  location: location
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: vnet.properties.subnets[0].id
      internal: false
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logWorkspace.properties.customerId
        sharedKey: logWorkspace.listKeys().primarySharedKey
      }
    }
  }
}

// ── Storage Account + File Share ────────────────────────────────────────

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowSharedKeyAccess: true
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  parent: fileService
  name: fileShareName
  properties: {
    shareQuota: 10
  }
}

// ── ACA Storage Mount ───────────────────────────────────────────────────

resource acaStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: acaEnv
  name: 'octoclawstorage'
  properties: {
    azureFile: {
      accountName: storageAccount.name
      accountKey: storageAccount.listKeys().keys[0].value
      shareName: fileShareName
      accessMode: 'ReadWrite'
    }
  }
}

// ── Key Vault (only when createKeyVault=true) ───────────────────────────

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = if (createKeyVault) {
  name: kvName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
  }
}

// Key Vault Secrets Officer for MI (only when KV is created here)
resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (createKeyVault) {
  name: guid(resourceGroup().id, identityName, keyVaultSecretsOfficerRole, 'kv')
  scope: keyVault
  properties: {
    principalId: managedIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficerRole)
    principalType: 'ServicePrincipal'
  }
}

// ── RBAC Assignments (resource-group scoped) ────────────────────────────

// Cognitive Services OpenAI User — for Azure OpenAI / Foundry models
resource aoaiRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(azureOpenAIEndpoint)) {
  name: guid(resourceGroup().id, managedIdentity.id, cognitiveServicesOpenAIUserRole)
  scope: resourceGroup()
  properties: {
    principalId: managedIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRole)
    principalType: 'ServicePrincipal'
  }
}

// Search Index Data Contributor — for Azure AI Search
resource searchRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(azureAISearchEndpoint)) {
  name: guid(resourceGroup().id, managedIdentity.id, searchIndexDataContributorRole)
  scope: resourceGroup()
  properties: {
    principalId: managedIdentity.properties.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRole)
    principalType: 'ServicePrincipal'
  }
}

// ── Container App ───────────────────────────────────────────────────────

var kvUrl = createKeyVault ? keyVault.properties.vaultUri : existingKeyVaultUrl

// Build env vars list — only include non-empty secret refs
var baseEnv = [
  { name: 'OCTOCLAW_AUTH_MODE', value: 'managed_identity' }
  { name: 'AZURE_CLIENT_ID', value: managedIdentity.properties.clientId }
  { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
  { name: 'OCTOCLAW_DATA_DIR', value: '/data' }
  { name: 'ADMIN_PORT', value: '8080' }
  { name: 'BOT_APP_ID', value: botAppId }
  { name: 'BOT_APP_TENANT_ID', value: botAppTenantId }
  { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAIEndpoint }
]

var kvEnv = !empty(kvUrl) ? [{ name: 'KEY_VAULT_URL', value: kvUrl }] : []
var peEnv = !empty(kvUrl) ? [{ name: 'KEYVAULT_USE_PRIVATE_ENDPOINT', value: 'true' }] : []

// Secret-backed env vars (ACA requires secrets to be defined even if empty)
var secretEnv = [
  { name: 'GITHUB_TOKEN', secretRef: 'github-token' }
  { name: 'BOT_APP_PASSWORD', secretRef: 'bot-app-password' }
  { name: 'ACS_CONNECTION_STRING', secretRef: 'acs-connection-string' }
]

resource acaApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: acaAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        { name: 'github-token', value: !empty(githubToken) ? githubToken : 'placeholder' }
        { name: 'bot-app-password', value: !empty(botAppPassword) ? botAppPassword : 'placeholder' }
        { name: 'acs-connection-string', value: !empty(acsConnectionString) ? acsConnectionString : 'placeholder' }
        { name: 'acr-password', value: !empty(acrPassword) ? acrPassword : 'placeholder' }
      ]
      registries: !empty(acrLoginServer) ? [
        {
          server: acrLoginServer
          username: acrUsername
          passwordSecretRef: 'acr-password'
        }
      ] : []
    }
    template: {
      containers: [
        {
          name: 'octoclaw'
          image: containerImage
          resources: {
            cpu: json(cpuCores)
            memory: memory
          }
          env: concat(baseEnv, kvEnv, peEnv, secretEnv)
          volumeMounts: [
            {
              volumeName: 'data'
              mountPath: '/data'
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
      volumes: [
        {
          name: 'data'
          storageName: acaStorage.name
          storageType: 'AzureFile'
        }
      ]
    }
  }
}

// ── Outputs ─────────────────────────────────────────────────────────────

output appFqdn string = acaApp.properties.configuration.ingress.fqdn
output appUrl string = 'https://${acaApp.properties.configuration.ingress.fqdn}'
output managedIdentityClientId string = managedIdentity.properties.clientId
output managedIdentityPrincipalId string = managedIdentity.properties.principalId
output keyVaultUrl string = kvUrl
output storageAccountName string = storageAccount.name