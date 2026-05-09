import asyncio
import json
import os
import logging
import requests
from aiohttp import web
import aiohttp
from duckduckgo_search import DDGS
import groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class XiaozhiGroqServer:
    def __init__(self):
        self.groq_client = groq.AsyncGroq(
            api_key=os.getenv("GROQ_API_KEY", "")
        )
        self.model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        self.tavily_api_key = os.getenv("TAVILY_API_KEY", "")

        self.search_keywords = [
            # Italiano
            "cerca", "trova", "notizie", "ultime", "oggi", "recenti",
            "informazioni su", "cosa è successo", "aggiornamento",
            "meteo", "tempo", "temperatura", "previsioni",
            "prezzo", "quotazione", "borsa", "bitcoin", "crypto",
            "chi ha vinto", "risultato", "punteggio", "partita",
            "quando è", "dove si trova", "quanto costa",
            # Inglese
            "search", "find", "news", "latest", "look up",
            "weather", "price", "score", "who won",
        ]

    # ------------------------------------------------------------------ #
    #  RICERCA WEB                                                         #
    # ------------------------------------------------------------------ #

    def search_tavily(self, query: str) -> str:
        try:
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.tavily_api_key,
                    "query": query,
                    "max_results": 3,
                    "search_depth": "basic"
                },
                timeout=8
            )
            results = resp.json().get("results", [])
            if not results:
                return ""
            parts = [f"- {r.get('title','')}: {r.get('content','')[:300]}" for r in results[:3]]
            return "\n".join(parts)
        except Exception as e:
            logger.error(f"Errore Tavily: {e}")
            return ""

    def search_duckduckgo(self, query: str) -> str:
        try:
            with DDGS() as ddgs:
                results = ddgs.text(query, max_results=4, region="it-it")
                parts = [f"- {r.get('title','')}: {r.get('body','')[:300]}" for r in results]
                return "\n".join(parts)
        except Exception as e:
            logger.error(f"Errore DuckDuckGo: {e}")
            return ""

    def search_web(self, query: str) -> str:
        if self.tavily_api_key:
            logger.info("🔍 Ricerca via Tavily")
            result = self.search_tavily(query)
            if result:
                return result
            logger.warning("Tavily vuoto, fallback DuckDuckGo")
        logger.info("🔍 Ricerca via DuckDuckGo")
        return self.search_duckduckgo(query)

    # ------------------------------------------------------------------ #
    #  LLM                                                                  #
    # ------------------------------------------------------------------ #

    async def ask_groq(self, user_text: str, web_context: str = "") -> str:
        if web_context:
            system_msg = (
                "Sei un assistente AI per dispositivi IoT, rispondi in italiano. "
                "Sii conciso (massimo 2-3 frasi brevi). "
                "Usa i risultati web per rispondere in modo aggiornato.\n\n"
                f"Risultati web:\n{web_context}"
            )
        else:
            system_msg = (
                "Sei un assistente AI per dispositivi IoT. "
                "Rispondi in italiano. Sii conciso (massimo 2-3 frasi brevi)."
            )
        try:
            response = await self.groq_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_text}
                ],
                max_tokens=300,
                temperature=0.6
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Errore Groq: {e}")
            return "Mi dispiace, non riesco a rispondere in questo momento."

    def needs_search(self, text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in self.search_keywords)

    # ------------------------------------------------------------------ #
    #  WEBSOCKET HANDLER (aiohttp)                                         #
    # ------------------------------------------------------------------ #

    async def websocket_handler(self, request):
        """Gestisce connessioni WebSocket da Xiaozhi."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        client_ip = request.remote
        logger.info(f"📱 Client connesso: {client_ip}")

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get("type", "")
                    text = data.get("text", "")

                    if msg_type == "ping":
                        await ws.send_json({"type": "pong"})
                        continue

                    if not text:
                        continue

                    logger.info(f"🎤 Ricevuto: {text[:80]}")

                    if self.needs_search(text):
                        logger.info("🔍 Ricerca web attivata")
                        web_results = await asyncio.to_thread(self.search_web, text)
                        answer = await self.ask_groq(text, web_results)
                    else:
                        logger.info("💬 Risposta diretta LLM")
                        answer = await self.ask_groq(text)

                    await ws.send_json({
                        "type": "tts",
                        "text": answer,
                        "session_id": data.get("session_id", "")
                    })
                    logger.info(f"✅ Risposta: {answer[:80]}")

                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "text": "Formato non valido"})

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")

        logger.info(f"❌ Client disconnesso: {client_ip}")
        return ws

    # ------------------------------------------------------------------ #
    #  HTTP HEALTH CHECK — risponde ai HEAD/GET di Render                  #
    # ------------------------------------------------------------------ #

    async def health_handler(self, request):
        return web.Response(
            text=json.dumps({
                "status": "ok",
                "model": self.model,
                "tavily": bool(self.tavily_api_key)
            }),
            content_type="application/json"
        )


async def main():
    server = XiaozhiGroqServer()
    port = int(os.getenv("PORT", 8000))

    app = web.Application()
    app.router.add_get("/", server.health_handler)       # health check Render
    app.router.add_get("/health", server.health_handler)
    app.router.add_get("/ws", server.websocket_handler)  # WebSocket Xiaozhi

    logger.info(f"🚀 Server avviato su porta {port}")
    logger.info(f"   Modello: {server.model}")
    logger.info(f"   Tavily: {'✅' if server.tavily_api_key else '⚠️  DuckDuckGo'}")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    await asyncio.Future()  # resta in ascolto


if __name__ == "__main__":
    asyncio.run(main())
