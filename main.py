import asyncio
import websockets
import json
import os
import logging
import requests
from duckduckgo_search import DDGS
import groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class XiaozhiGroqServer:
    def __init__(self):
        self.groq_client = groq.AsyncGroq(
            api_key=os.getenv("GROQ_API_KEY", "")
        )
        # Modello più capace e stabile su Groq
        self.model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        
        # API key Tavily (opzionale, fallback su DuckDuckGo)
        self.tavily_api_key = os.getenv("TAVILY_API_KEY", "")
        
        # Parole chiave ampliate per trigger ricerca
        self.search_keywords = [
            # Italiano
            "cerca", "trova", "notizie", "ultime", "oggi", "recenti",
            "informazioni su", "cosa è successo", "aggiornamento",
            "meteo", "tempo", "temperatura", "previsioni",
            "prezzo", "quotazione", "borsa", "bitcoin",
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
        """Ricerca via Tavily (più affidabile sui cloud provider)."""
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
            parts = []
            for r in results[:3]:
                title = r.get("title", "")
                content = r.get("content", "")[:300]
                parts.append(f"- {title}: {content}")
            return "\n".join(parts)
        except Exception as e:
            logger.error(f"Errore Tavily: {e}")
            return ""

    def search_duckduckgo(self, query: str) -> str:
        """Fallback su DuckDuckGo (gratuito, no API key)."""
        try:
            with DDGS() as ddgs:
                results = ddgs.text(query, max_results=4, region="it-it")
                parts = []
                for r in results:
                    title = r.get("title", "N/A")
                    body = r.get("body", "")[:300]
                    parts.append(f"- {title}: {body}")
                return "\n".join(parts)
        except Exception as e:
            logger.error(f"Errore DuckDuckGo: {e}")
            return ""

    def search_web(self, query: str) -> str:
        """
        Usa Tavily se disponibile (più stabile su Render),
        altrimenti DuckDuckGo come fallback.
        """
        if self.tavily_api_key:
            logger.info("🔍 Ricerca via Tavily")
            result = self.search_tavily(query)
            if result:
                return result
            logger.warning("Tavily vuoto, fallback su DuckDuckGo")

        logger.info("🔍 Ricerca via DuckDuckGo")
        return self.search_duckduckgo(query)

    # ------------------------------------------------------------------ #
    #  LLM                                                                  #
    # ------------------------------------------------------------------ #

    async def ask_groq(self, user_text: str, web_context: str = "") -> str:
        """Invia la richiesta a Groq con o senza risultati web."""
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

    # ------------------------------------------------------------------ #
    #  LOGICA DI ROUTING                                                   #
    # ------------------------------------------------------------------ #

    def needs_search(self, text: str) -> bool:
        """Controlla se la domanda richiede dati in tempo reale."""
        text_lower = text.lower()
        return any(kw in text_lower for kw in self.search_keywords)

    # ------------------------------------------------------------------ #
    #  WEBSOCKET HANDLER                                                   #
    # ------------------------------------------------------------------ #

    async def handler(self, websocket, path):
        """Gestisce ogni connessione WebSocket da Xiaozhi."""
        client_ip = websocket.remote_address[0]
        logger.info(f"📱 Client connesso: {client_ip}")

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "")
                    text = data.get("text", "")

                    # Risposta a ping / keepalive
                    if msg_type == "ping":
                        await websocket.send(json.dumps({"type": "pong"}))
                        continue

                    if not text:
                        continue

                    logger.info(f"🎤 Ricevuto: {text[:80]}")

                    # Scegli la strategia
                    if self.needs_search(text):
                        logger.info("🔍 Ricerca web attivata")
                        web_results = await asyncio.to_thread(self.search_web, text)
                        answer = await self.ask_groq(text, web_results)
                    else:
                        logger.info("💬 Risposta diretta LLM")
                        answer = await self.ask_groq(text)

                    response = {
                        "type": "tts",
                        "text": answer,
                        "session_id": data.get("session_id", "")
                    }
                    await websocket.send(json.dumps(response))
                    logger.info(f"✅ Risposta: {answer[:80]}")

                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "text": "Formato messaggio non valido"
                    }))

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"❌ Client disconnesso: {client_ip}")


async def main():
    server = XiaozhiGroqServer()
    port = int(os.getenv("PORT", 8000))
    logger.info(f"🚀 Server Xiaozhi+Groq su porta {port}")
    logger.info(f"   Modello: {server.model}")
    logger.info(f"   Tavily: {'✅ configurato' if server.tavily_api_key else '⚠️  non configurato (uso DuckDuckGo)'}")

    async with websockets.serve(server.handler, "0.0.0.0", port):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
