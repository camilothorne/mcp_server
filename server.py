import os
from mcp.server import MCPServer
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from settings import DBPEDIA_SPARQL_ENDPOINT, DBLP_SPARQL_ENDPOINT

import hashlib
import os
import re
import threading
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor

mcp = MCPServer("Knowledge Graph Agent")

"""

This is a simple example of a knowledge graph MCP server that can query DBpedia and DBLP.
and return the results in SPARQL Results JSON format.
It uses the mcp library to create a server that can be queried via HTTP.
The server exposes two tools:

- query_dbpedia: queries DBpedia for a given SPARQL query and returns the results
- query_dblp_topic: queries DBLP for a given topic and returns the top 10 publications and their citation counts

"""

# -----------------------------------------------------------------------------
# GLOBAL ANUBIS SESSION CONFIGURATION
# -----------------------------------------------------------------------------
class GlobalAnubisManager:
    """Manages global state and HTTP cookie persistence across all MCP tool tasks."""
    def __init__(self):
        self.cookie_processor = HTTPCookieProcessor()
        # Thread-safe cookie storage opener
        self.opener = build_opener(self.cookie_processor)
        self.lock = threading.Lock()
        self.common_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/sparql-results+json, text/html, */*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        }

    @staticmethod
    def _solve_pow(seed: str, difficulty: int) -> int:
        """Brute-force nonce locally to clear the Anubis security check."""
        nonce = 0
        prefix = "0" * difficulty
        while True:
            target = f"{seed}{nonce}".encode("utf-8")
            if hashlib.sha256(target).hexdigest().startswith(prefix):
                return nonce
            nonce += 1

    def handle_challenge(self, endpoint_url: str, html_body: str):
        """Thread-safe interception loop to parse, solve, and lock in the Anubis session token."""
        with self.lock:
            # Re-verify inside lock to prevent redundant solvers from racing
            # Extract challenge criteria embedded in the target page
            seed_match = re.search(r'["\']?seed["\']?\s*:\s*["\']([^"\']+)["\']', html_body)
            diff_match = re.search(r'["\']?difficulty["\']?\s*:\s*(\d+)', html_body)
            
            if not (seed_match and diff_match):
                raise RuntimeError("Failed to extract active Anubis security metadata.")
                
            seed = seed_match.group(1)
            difficulty = int(diff_match.group(1))
            
            # Compute hash verification
            solution_nonce = self._solve_pow(seed, difficulty)
            
            # Post credentials back to the validation checkpoint
            verify_url = f"{endpoint_url.rstrip('/')}/.anubis/verify"
            verify_data = urlencode({"nonce": solution_nonce, "seed": seed}).encode("utf-8")
            verify_req = Request(verify_url, data=verify_data, headers=self.common_headers, method="POST")
            
            with self.opener.open(verify_req) as verify_res:
                verify_res.read()  # Cookie payload auto-binds directly into self.cookie_processor


# Instantiate a persistent global manager instance 
ANUBIS_MANAGER = GlobalAnubisManager()

#DBLP_SPARQL_ENDPOINT = os.getenv("DBLP_SPARQL_ENDPOINT", "https://dblp.org")

# -----------------------------------------------------------------------------
# REFACTORED MCP TOOL WITH GLOBAL COOKIE MEMORY
# -----------------------------------------------------------------------------
@mcp.tool()
def query_dblp_topic(topic: str) -> str:
    """Query DBLP for a topic while utilizing a global session firewall cookie cache."""
    query = f"""
        PREFIX dblp: <https://dblp.org>
        PREFIX cito: <http://purl.org>
        PREFIX rdfs: <http://w3.org>
        PREFIX rdf: <http://w3.org>
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
        
    payload = urlencode({"query": query}).encode("utf-8")
    
    # 1. Attempt using the active global cookie session state
    req = Request(DBLP_SPARQL_ENDPOINT, data=payload, headers=ANUBIS_MANAGER.common_headers, method="POST")
    try:
        with ANUBIS_MANAGER.opener.open(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            if "anubis" not in body.lower():
                return body
            # If "anubis" string is detected inside a 200 OK, fallback to the solver below
            html_challenge = body
    except Exception:
        # Fallback to fetching a fresh challenge token if the active cookie was revoked/expired (e.g. 403/422)
        challenge_req = Request(DBLP_SPARQL_ENDPOINT, headers={"User-Agent": ANUBIS_MANAGER.common_headers["User-Agent"]})
        with ANUBIS_MANAGER.opener.open(challenge_req) as chal_res:
            html_challenge = chal_res.read().decode("utf-8")

    # 2. Trigger the Global Solution layer (updates cookies shared across all workers)
    ANUBIS_MANAGER.handle_challenge(DBLP_SPARQL_ENDPOINT, html_challenge)
    
    # 3. Re-execute original payload utilizing the newly cached credentials
    retry_req = Request(DBLP_SPARQL_ENDPOINT, data=payload, headers=ANUBIS_MANAGER.common_headers, method="POST")
    with ANUBIS_MANAGER.opener.open(retry_req, timeout=30) as final_res:
        return final_res.read().decode("utf-8")


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

# @mcp.tool()
# def query_dblp_topic(topic: str) -> str:
#     """Query DBLP for a topic."""
#     query = f"""
#         PREFIX dblp: <https://dblp.org/rdf/schema#>
#         PREFIX cito: <http://purl.org/spar/cito/>
#         PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
#         PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
#         SELECT ?publ ?label (COUNT(?citation) as ?cites) WHERE {{
#             ?publ rdf:type dblp:Publication .
#             ?publ dblp:title ?title .
#             FILTER CONTAINS(?title, "{topic}") .
#             ?publ dblp:omid ?omid .
#             ?publ rdfs:label ?label .
#             ?citation rdf:type cito:Citation .
#             ?citation cito:hasCitedEntity ?omid .
#         }}
#         GROUP BY ?publ ?label
#         ORDER BY DESC(?cites)
#         LIMIT 10"""
#     request = Request(
#         DBLP_SPARQL_ENDPOINT,
#         data=urlencode({"query": query}).encode("utf-8"),
#         headers={
#             "Accept": "application/sparql-results+json",
#             "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
#         },
#         method="POST",
#     )
#     with urlopen(request, timeout=30) as response:
#         return response.read().decode("utf-8")

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