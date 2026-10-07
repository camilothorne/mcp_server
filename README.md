# Knowledge Graph MCP Server

An MCP server that exposes SPARQL-backed knowledge graph tools for DBpedia and DBLP. It uses the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) and serves Streamable HTTP.

## Tools

- `query_dbpedia(query)`: Sends the supplied SPARQL query to DBpedia as a URL-encoded HTTP GET and returns SPARQL Results JSON.
- `query_dblp_topic(topic)`: Searches DBLP publication titles for the supplied topic and returns up to 10 results with citation counts.
- `query_arxiv(topic)`: Searches arXiv for papers matching the topic that were submitted during the current UTC calendar week, returning Atom XML.

DBpedia and DBLP use public SPARQL endpoints configured in `settings.py`. The arXiv tool uses the public arXiv API.

## Run Locally

Requires Python 3.11 or later.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

The MCP Streamable HTTP endpoint is `http://localhost:8080/mcp`. The server uses the `PORT` environment variable when set, and defaults to port 8080.

## Build and Run with Docker

```bash
docker build -t knowledge-graph-mcp .
docker run --rm -p 8080:8080 -e PORT=8080 knowledge-graph-mcp
```

Connect an MCP client to `http://localhost:8080/mcp` using Streamable HTTP.

## Deploy to Cloud Run

The `compile_and_test_gcp.yaml` GitHub Actions workflow builds and pushes the image to Artifact Registry. It deploys when a push commit message contains `[deploy]` or when a pull request is merged. Configure the workflow's `docker-env` environment with the `GCP_SA_KEY` and `GCP_PROJECT` secrets.

The workflow configures the service to listen on port 8080 and disables unauthenticated access. Callers therefore need Cloud Run invoker permission and a valid Google identity token. The MCP endpoint is `https://<cloud-run-service-url>/mcp`.
