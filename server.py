import os
from mcp.server import MCPServer
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from settings import DBPEDIA_SPARQL_ENDPOINT, DBLP_SPARQL_ENDPOINT

mcp = MCPServer("Knowledge Graph Agent")

@mcp.tool()
def query_dbpedia(query: str) -> str:
    """Run a SPARQL query against DBpedia and return SPARQL Results JSON."""
    request = Request(
        DBPEDIA_SPARQL_ENDPOINT,
        data=urlencode({"query": query}).encode("utf-8"),
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")

@mcp.tool()
def query_dblp_topic(topic: str) -> str:
    """Query DBLP for a topic."""
    query = f"""
        PREFIX dblp: <https://dblp.org/rdf/schema#>
        PREFIX cito: <http://purl.org/spar/cito/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        SELECT ?publ ?label (COUNT(?citation) as ?cites) WHERE {{
            ?publ rdf:type dblp:Publication .
            ?publ dblp:title ?title .
            FILTER CONTAINS(?title, "{topic}") .
            ?publ dblp:omid ?omid .
            ?publ rdfs:label ?label .
            ?citation rdf:type cito:Citation .
            ?citation cito:hasCitedEntity ?omid .
        }}
        GROUP BY ?publ ?label
        ORDER BY DESC(?cites)
        LIMIT 10"""
    request = Request(
        DBLP_SPARQL_ENDPOINT,
        data=urlencode({"query": query}).encode("utf-8"),
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")

# @mcp.resource("greeting://{name}")
# def greeting(name: str) -> str:
#     """Greet someone by name."""
#     return f"Hello, {name}!"


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )