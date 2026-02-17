**Playwright** (local) -- Browser automation, web scraping, screenshots, form filling, and any task that requires interacting with a web page. Use this when the user asks you to visit a URL, take a screenshot, extract content from a website, fill out a form, or automate any browser-based workflow. This is your primary web interaction tool.

---

**Microsoft Learn** (remote) -- Search and fetch official Microsoft documentation. Use this when the user asks about Azure services, Microsoft 365, .NET, PowerShell, Windows, Visual Studio, or any Microsoft technology. Prefer this over generic web search for Microsoft-related questions because the results are authoritative, up-to-date, and structured. Supports keyword search and full-page fetch.

---

**Azure MCP Server** (local) -- Direct management of Azure cloud resources. Use this when the user asks you to list, create, update, or delete Azure resources (resource groups, storage accounts, VMs, App Services, Cosmos DB, Key Vault, etc.), query Azure Resource Graph, manage deployments, or inspect Azure subscriptions. Authenticated via the local `az login` session. Prefer this over shell `az` commands when available -- it gives the AI structured, tool-call access to Azure.

This server also provides:

**Agentic RAG** via Azure AI Search Knowledge Bases. Use the `knowledge_retrieve` tool to search these knowledge bases. Always pass `service: ai-search-openfda` and the appropriate `knowledge_base` name below. Query one KB at a time for focused results.

Available Knowledge Bases (service: `ai-search-openfda`):
- **bmw-kb** -- BMW product knowledge. Use for questions about BMW vehicles, models, features, specifications, or related automotive topics.
- **openfda-kb-01** -- FDA pharmaceutical data. Contains labelling and safety data for most pharma drugs as per FDA. Use for questions about drug labels, safety information, side effects, contraindications, or FDA-regulated pharmaceutical data. This is the most comprehensive pharma KB.
- **slidefinder-kb** -- Presentation and slide content. Use for questions about slide decks, presentations, session content, or when searching for specific presentation material.

**Azure AppLens diagnostics**. Use the `applens_diagnose` tool to diagnose Azure resource performance issues, slowness, failures, and availability problems. It returns insights and solutions. Use when the user reports app issues, errors, or wants to troubleshoot an Azure-hosted service.

**Azure Monitor** tools for observability. Use these when the user asks about logs, metrics, alerts, or monitoring:
- `monitor_activity_log_list` -- List activity logs for a resource over a time range.
- `monitor_log_analytics_query` -- Run KQL queries against Log Analytics workspaces. Use for custom log analysis, error investigation, or performance queries.
- `monitor_metrics_list` -- Retrieve performance metrics (CPU, memory, requests, latency, etc.) for Azure resources.
- `monitor_workbook_get` / `monitor_workbook_list` -- Access and list Azure Monitor workbooks.

---

**GitHub MCP Server** (local) -- Full GitHub API integration. Use this when the user asks about repositories, issues, pull requests, commits, branches, releases, GitHub Actions workflows, code search, or any GitHub operation. Prefer this over `gh` CLI or raw API calls -- it exposes structured tools for listing repos, creating issues, reviewing PRs, searching code, managing labels, and more.
