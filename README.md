# EnergyIQ FastMCP

This project is a **POC (Proof of Concept)** FastMCP server that exposes tools for:
- Site metadata (`site_details`)
- Site tags (`get_site_tags`)
- Time-series data (`get_data`)

It demonstrates how Claude (or any MCP-enabled LLM) can call multiple tools to fetch structured data.

---

## Project Setup

### Requirements
- Python **>= 3.10**
- [uv](https://docs.astral.sh/uv/) (recommended for fast dependency management)

### Install dependencies
Clone the repo and install dependencies into your virtual environment:

```bash
uv pip install -e .
```

### Environment

Create a .env file in the energyiq_fastmcp/ directory with API credentials if you want real data:

BASE_URL=https://your-api.example.com/
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret

If these values are missing, the server will automatically return mocked data for testing/demo.


### Running Locally

Start MCP server from the project root

```bash
python -m energyiq_fastmcp.server
```
