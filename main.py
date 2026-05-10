import asyncio
import json
import os
import logging
import requests
from aiohttp import web
import aiohttp
import websockets

# Supporta sia il nuovo pacchetto ddgs che il vecchio duckduckgo_search
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MCP_ENDPOINT = os.getenv("MCP_ENDPOINT", "")


def search_web(query: str) -> str:
    """Cerca su DuckDuckGo e restituisce risultati compatti."""
    if not query or not query.strip():
        return "Query di ricerca vuota."
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query.strip(), max_results=4, region="it-it")
            parts = [f"- {r.get('title','')}: {r.get('body','')[:300]}" for r in results]
            return "\n".join(parts) if parts else "Nessun risultato trovato."
    except Exception as e:
        logger.error(f"Errore DuckDuckGo: {e}")
        return f"Errore ricerca: {str(e)}"


# ------------------------------------------------------------------ #
#  MCP SERVER                                                          #
# ------------------------------------------------------------------ #

def handle_mcp_request(request_data: dict) -> dict:
    method = request_data.get("method", "")
    req_id = request_data.get("id")

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "web_search",
                        "description": (
                            "Cerca informazioni aggiornate sul web. "
                            "Usare per notizie, meteo, prezzi, eventi recenti, "
                            "risultati sportivi e qualsiasi dato in tempo reale."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "La query di ricerca in italiano o inglese"
                                }
                            },
                            "required": ["query"]
                        }
                    }
                ]
            }
        }

    elif method == "tools/call":
        tool_name = request_data.get("params", {}).get("name", "")
        # Qwen3 può mandare gli argomenti in "arguments" o "input"
        params = request_data.get("params", {})
        arguments = params.get("arguments") or params.get("input") or params.get("parameters") or {}

        # Log completo per debug
        logger.info(f"📦 params completi: {json.dumps(params)}")

        if tool_name == "web_search":
            query = arguments.get("query", "").strip()
            logger.info(f"🔍 Ricerca: '{query}'")

            if not query:
                result = "Nessuna query ricevuta."
            else:
                result = search_web(query)

            logger.info(f"✅ Trovato: {result[:100]}...")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result}]
                }
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Tool '{tool_name}' non trovato"}
            }

    elif method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "xiaozhi-websearch", "version": "1.0.0"}
            }
        }

    elif method == "notifications/initialized":
        # Notifica, non richiede risposta
        return None

    else:
        logger.warning(f"Metodo sconosciuto: {method} — dati: {json.dumps(request_data)}")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Metodo '{method}' non supportato"}
        }


async def mcp_bridge():
    if not MCP_ENDPOINT:
        logger.warning("⚠️  MCP_ENDPOINT non configurato.")
        return

    while True:
        try:
            logger.info("🔌 Connessione a xiaozhi.me MCP endpoint...")
            async with websockets.connect(MCP_ENDPOINT) as ws:
                logger.info("✅ Connesso all'MCP endpoint!")
                async for message in ws:
                    try:
                        data = json.loads(message)
                        logger.info(f"📨 MCP request: {data.get('method','?')} (id={data.get('id')})")
                        response = handle_mcp_request(data)
                        if response is not None:
                            await ws.send(json.dumps(response))
                    except Exception as e:
                        logger.error(f"Errore gestione messaggio: {e}")
        except Exception as e:
            logger.error(f"❌ Connessione MCP persa: {e}. Riprovo tra 5s...")
            await asyncio.sleep(5)


async def health_handler(request):
    return web.Response(
        text=json.dumps({"status": "ok", "mcp_endpoint": bool(MCP_ENDPOINT)}),
        content_type="application/json"
    )


async def main():
    port = int(os.getenv("PORT", 8000))
    asyncio.create_task(mcp_bridge())

    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)

    logger.info(f"🚀 Server avviato su porta {port}")
    logger.info(f"   MCP Endpoint: {'✅ configurato' if MCP_ENDPOINT else '⚠️  non configurato'}")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
