using 'main.bicep'

param baseName = 'octoclaw'
param location = 'swedencentral'
param containerImage = 'ghcr.io/your-org/octoclaw:latest'
param githubToken = ''
param cpuCores = '1.0'
param memory = '2Gi'
param minReplicas = 1
param maxReplicas = 1

// Key Vault — set createKeyVault=true to provision one, or provide existing URL
param createKeyVault = false
param existingKeyVaultUrl = ''

// Optional: set these to use pre-existing resources
param azureOpenAIEndpoint = ''
param azureAISearchEndpoint = ''
param acsConnectionString = ''
param botAppId = ''
param botAppPassword = ''
param botAppTenantId = ''