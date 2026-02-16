# OctoClaw — Azure Container Apps Deployment

Deploy OctoClaw on Azure Container Apps with Managed Identity, VNet integration, and persistent Azure Files storage.

## What Gets Created

| Resource | Purpose |
|----------|---------|
| User-Assigned Managed Identity | Authentication to all Azure services (no passwords) |
| VNet (10.0.0.0/16) | Network isolation for ACA |
| ACA Environment | Container Apps hosting environment |
| Container App | OctoClaw application with external HTTPS ingress |
| Storage Account + File Share | Persistent data at `/data` (sessions, memory, config) |
| Log Analytics Workspace | Container logs |
| Key Vault (optional) | Secret storage — set `createKeyVault=true` or use existing |
| RBAC Role Assignments | MI → Cognitive Services OpenAI User, Search Index Data Contributor |

## Prerequisites

1. Azure CLI installed and logged in
2. A resource group created
3. OctoClaw container image pushed to a registry
4. A GitHub token (PAT or fine-grained) for Copilot CLI

## Quick Start

```bash
# Create the resource group
az group create --name rg-octoclaw --location swedencentral

# Deploy (minimal — ACA + Storage + VNet + MI only)
az deployment group create \
  --resource-group rg-octoclaw \
  --template-file deploy/main.bicep \
  --parameters \
    containerImage='your-registry.azurecr.io/octoclaw:latest' \
    location='swedencentral'

# Get the app URL
az deployment group show \
  --resource-group rg-octoclaw \
  --name main \
  --query properties.outputs.appUrl.value -o tsv
```

## With Existing Foundry / AI Search Resources

Pass existing resource endpoints to configure RBAC automatically:

```bash
az deployment group create \
  --resource-group rg-octoclaw \
  --template-file deploy/main.bicep \
  --parameters \
    containerImage='your-registry.azurecr.io/octoclaw:latest' \
    location='swedencentral' \
    azureOpenAIEndpoint='https://my-aoai.openai.azure.com' \
    azureAISearchEndpoint='https://my-search.search.windows.net' \
    githubToken='ghp_...'
```

> **Note:** The RBAC assignments above are scoped to the deployment resource group.
> If your Foundry/Search resources are in a different RG, you must manually assign
> `Cognitive Services OpenAI User` and `Search Index Data Contributor` roles to the
> MI principal (output: `managedIdentityPrincipalId`) on those resources.

## Post-Deployment

Update the container app with its own ingress URL so bot endpoint routing works:

```bash
APP_URL=$(az deployment group show \
  --resource-group rg-octoclaw --name main \
  --query properties.outputs.appUrl.value -o tsv)

az containerapp update \
  --name octoclaw-app \
  --resource-group rg-octoclaw \
  --set-env-vars "OCTOCLAW_INGRESS_URL=$APP_URL"
```

## Architecture

```
Internet ──► ACA External Ingress (HTTPS)
                │
                ▼
         ┌──────────────┐
         │  OctoClaw     │──► Azure Files (/data)
         │  Container    │──► Azure OpenAI / Foundry (MI auth)
         │  App          │──► Azure AI Search (MI auth)
         └──────────────┘
                │
           VNet (10.0.0.0/16)
```