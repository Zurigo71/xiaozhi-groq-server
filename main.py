import asyncio
import json
import os
import logging
import requests
from aiohttp import web
import aiohttp
import websockets
from duckduckgo_search import DDGS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MCP_ENDPOINT = os.getenv("MCP_ENDPOINT", "")


def search_web(query: str) -> str:
    """Cerca su DuckDuckGo e restituisce risultati compatti."""
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=4, region="it-it")
            parts = [f"- {r.get('title','')}: {r.get('body','')[:300]}" for r in results]
            return "\n".join(parts) if parts else "Nessun risultato trovato."
    except Exception as e:
        logger.error(f"Errore DuckDuckGo: {e}")
        return f"Errore ricerca: {str(e)}"


# ------------------------------------------------------------------ #
#  MCP SERVER — espone il tool web_search a xiaozhi.me               #
# ------------------------------------------------------------------ #

def handle_mcp_request(request_data: dict) -> dict:
    """Gestisce le richieste MCP in arrivo da xiaozhi.me."""
    method = request_data.get("method", "")
    req_id = request_data.get("id")

    # Il modello vuole sapere quali tool sono disponibili
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

    # Il modello vuole eseguire una ricerca
    elif method == "tools/call":
        tool_name = request_data.get("params", {}).get("name", "")
        arguments = request_data.get("params", {}).get("arguments", {})

        if tool_name == "web_search":
            query = arguments.get("query", "")
            logger.info(f"🔍 Ricerca: {query}")
            result = search_web(query)
            logger.info(f"✅ Trovato: {result[:80]}...")
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

    # Inizializzazione MCP
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

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Metodo '{method}' non supportato"}
        }


async def mcp_bridge():
    """
    Si connette all'endpoint MCP di xiaozhi.me via WebSocket
    e risponde alle chiamate del modello LLM.
    """
    if not MCP_ENDPOINT:
        logger.warning("⚠️  MCP_ENDPOINT non configurato, bridge disabilitato.")
        return

    while True:
        try:
            logger.info(f"🔌 Connessione a xiaozhi.me MCP endpoint...")
            async with websockets.connect(MCP_ENDPOINT) as ws:
                logger.info("✅ Connesso all'MCP endpoint!")

                async for message in ws:
                    try:
                        data = json.loads(message)
                        logger.info(f"📨 MCP request: {data.get('method','?')} (id={data.get('id')})")
                        response = handle_mcp_request(data)
                        await ws.send(json.dumps(response))
                    except Exception as e:
                        logger.error(f"Errore gestione messaggio: {e}")

        except Exception as e:
            logger.error(f"❌ Connessione MCP persa: {e}. Riprovo tra 5s...")
            await asyncio.sleep(5)


# ------------------------------------------------------------------ #
#  HTTP — health check per Render                                     #
# ------------------------------------------------------------------ #

async def health_handler(request):
    return web.Response(
        text=json.dumps({
            "status": "ok",
            "mcp_endpoint": bool(MCP_ENDPOINT),
            "bridge": "running"
        }),
        content_type="application/json"
    )


async def main():
    port = int(os.getenv("PORT", 8000))

    # Avvia il bridge MCP in background
    asyncio.create_task(mcp_bridge())

    # Avvia HTTP server per health check Render
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
