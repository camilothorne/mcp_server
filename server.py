import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from mcp.server import MCPServer
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.request import urlopen
from settings import DBPEDIA_SPARQL_ENDPOINT, DBLP_SPARQL_ENDPOINT

mcp = MCPServer("Knowledge Graph Agent")

"""

This is a simple example of a knowledge graph MCP server that can query DBpedia, DBLP, etc.
and return the results in JSON format.
It uses the MCP Python library to create a server that can be queried via HTTP.
The server exposes these tools:

- query_dbpedia: queries DBpedia for a given SPARQL query and returns the results
- query_arxiv: queries arXiv for papers on a given topic submitted during the current UTC week and returns the results in Atom XML format
- query_dblp_topic: queries DBLP for a given topic and returns the top 10 publications and their citation counts

"""

# -----------------------------------------------------------------------------
# GLOBAL ANUBIS SESSION CONFIGURATION
# -----------------------------------------------------------------------------

class AnubisHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = {}
        self.current_script_id = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.current_script_id = dict(attrs).get("id")
            if self.current_script_id:
                self.scripts[self.current_script_id] = []

    def handle_data(self, data):
        if self.current_script_id:
            self.scripts[self.current_script_id].append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.current_script_id = None

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
    def _solve_pow(random_data: str, difficulty: int) -> tuple[int, str]:
        """Return a nonce and its SHA-256 proof for an Anubis challenge."""
        nonce = 0
        prefix = "0" * difficulty
        while True:
            digest = hashlib.sha256(f"{random_data}{nonce}".encode("utf-8")).hexdigest()
            if digest.startswith(prefix):
                return nonce, digest
            nonce += 1

    def handle_challenge(self, endpoint_url: str, html_body: str):
        """Solve an Anubis challenge and persist its authorization cookie."""
        with self.lock:
            parser = AnubisHTMLParser()
            parser.feed(html_body)
            try:
                challenge_data = json.loads("".join(parser.scripts["anubis_challenge"]))
                challenge = challenge_data["challenge"]
                challenge_id = challenge["id"]
                random_data = challenge["randomData"]
                difficulty = int(challenge_data["rules"]["difficulty"])
                base_prefix = json.loads("".join(parser.scripts["anubis_base_prefix"]))
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError("Failed to extract active Anubis security metadata.") from error

            started_at = time.perf_counter()
            solution_nonce, response_hash = self._solve_pow(random_data, difficulty)
            elapsed_time = max(1, round((time.perf_counter() - started_at) * 1000))

            endpoint = urlsplit(endpoint_url)
            api_prefix = base_prefix.rstrip("/")
            verify_path = f"{api_prefix}/.within.website/x/cmd/anubis/api/pass-challenge"
            if not verify_path.startswith("/"):
                verify_path = f"/{verify_path}"
            verify_url = f"{endpoint.scheme}://{endpoint.netloc}{verify_path}"
            redir = "/"
            verify_params = urlencode({
                "id": challenge_id,
                "response": response_hash,
                "nonce": solution_nonce,
                "redir": redir,
                "elapsedTime": elapsed_time,
            })
            verify_req = Request(
                f"{verify_url}?{verify_params}",
                headers={
                    "User-Agent": self.common_headers["User-Agent"],
                    "Accept": "text/html, */*",
                    "Referer": endpoint_url,
                },
                method="GET",
            )
            with self.opener.open(verify_req) as verify_res:
                verify_res.read()

# Instantiate a persistent global manager instance 
ANUBIS_MANAGER = GlobalAnubisManager()

# -----------------------------------------------------------------------------
# REFACTORED MCP TOOLs WITH GLOBAL COOKIE MEMORY
# -----------------------------------------------------------------------------

@mcp.tool()
def query_dblp_topic(topic: str) -> str:
    """Query DBLP for a topic while utilizing a global session firewall cookie cache."""
    escaped_topic = topic.replace("\\", "\\\\").replace('"', '\\"')
    query = f"""
        PREFIX dblp: <https://dblp.org/rdf/schema#>
        PREFIX cito: <http://purl.org/spar/cito/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
        SELECT ?publ ?label (COUNT(?citation) as ?cites) WHERE {{
            ?publ rdf:type dblp:Publication .
            ?publ dblp:title ?title .
            FILTER CONTAINS(LCASE(STR(?title)), LCASE("{escaped_topic}")) .
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
        f"{DBPEDIA_SPARQL_ENDPOINT}?{urlencode({'query': query})}",
        headers={"Accept": "application/sparql-results+json"},
        method="GET",
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")

@mcp.tool()
def query_arxiv(topic: str) -> str:
    """Search arXiv for papers on a topic submitted during the current UTC week."""
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    escaped_topic = topic.replace("\\", "\\\\").replace('"', '\\"')
    search_query = (
        f'all:"{escaped_topic}" AND '
        f'submittedDate:[{week_start:%Y%m%d}0000 TO {week_end:%Y%m%d}2359]'
    )
    params = urlencode({
        "search_query": search_query,
        "start": 0,
        "max_results": 20,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    request = Request(
        f"https://export.arxiv.org/api/query?{params}",
        headers={"User-Agent": "KnowledgeGraphMCP/1.0"},
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")

# -----------------------------------------------------------------------------
# SERVER ENTRYPOINT
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )