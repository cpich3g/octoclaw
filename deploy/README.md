# OctoClaw — Azure Container Apps Deployment

Deploy OctoClaw on Azure Container Apps with Managed Identity, VNet integration, and persistent Azure Files storage.

## What Gets Created

| Resource | Purpose |
|----------|---------|
| User-Assigned Managed Identity | Authentication to all Azure services (no passwords) |
| VNet (10.0.0.0/16) | Network isolation for ACA, Storage, Key Vault |
| ACA Environment | Container Apps hosting environment |
| Container App | OctoClaw application with external HTTPS ingress |
| Storage Account + File Share | Persistent data at `/data` (sessions, memory, config) |
| Key Vault | Secret storage (optional — can use existing) |
| Log Analytics Workspace | Container logs |
| RBAC Role Assignments | MI → KV Secrets Officer, Cognitive Services User, Search Contributor |

## Prerequisites

1. Azure CLI installed and logged in
2. A resource group created
3. OctoClaw container image pushed to a registry
4. A GitHub token (PAT or fine-grained) for Copilot CLI

## Quick Start

```bash
# Create the resource group
az group create --name octoclaw-rg --location eastus

# Deploy
az deployment group create \
  --resource-group octoclaw-rg \
  --template-file main.bicep \
  --parameters \
    containerImage='ghcr.io/your-org/octoclaw:latest' \
    githubToken='ghp_...'

# Get the app URL
az deployment group show \
  --resource-group octoclaw-rg \
  --name main \
  --query properties.outputs.appUrl.value -o tsv
```

## Using Pre-Existing Resources

Pass existing resource endpoints to skip creation:

```bash
az deployment group create \
  --resource-group octoclaw-rg \
  --template-file main.bicep \
  --parameters \
    containerImage='ghcr.io/your-org/octoclaw:latest' \
    githubToken='ghp_...' \
    existingKeyVaultUrl='https://my-kv.vault.azure.net' \
    azureOpenAIEndpoint='https://my-aoai.openai.azure.com' \
    azureAISearchEndpoint='https://my-search.search.windows.net' \
    botAppId='00000000-0000-0000-0000-000000000000' \
    botAppPassword='...' \
    botAppTenantId='00000000-0000-0000-0000-000000000000'
```

> **Note:** When using existing resources, ensure the Managed Identity (output: `managedIdentityPrincipalId`) has the appropriate RBAC roles on those resources.

## Post-Deployment

After deployment, update the `OCTOCLAW_INGRESS_URL` on the container app to its own FQDN so the bot endpoint URL is correctly formed:

```bash
APP_URL=$(az deployment group show \
  --resource-group octoclaw-rg --name main \
  --query properties.outputs.appUrl.value -o tsv)

az containerapp update \
  --name octoclaw-app \
  --resource-group octoclaw-rg \
  --set-env-vars "OCTOCLAW_INGRESS_URL=$APP_URL"
```

## Architecture

```
Internet ──► ACA External Ingress (HTTPS)
                │
                ▼
         ┌──────────────┐
         │  OctoClaw     │──► Azure Files (/data)
         │  Container    │──► Key Vault (MI auth)
         │  App          │──► Azure OpenAI (MI auth)
         └──────────────┘──► Azure AI Search (MI auth)
                │
           VNet (10.0.0.0/16)
```