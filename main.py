import asyncio
import websockets
import json
import os
import logging
from duckduckgo_search import AsyncDDGS
import groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class XiaozhiGroqServer:
    def __init__(self):
        self.ddgs = AsyncDDGS()
        self.groq_client = groq.AsyncGroq(
            api_key=os.getenv("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", "")
        )
        # Modello leggero e veloce su Groq
        self.model = "llama-3.2-3b-preview"
        
        # Parole chiave che attivano la ricerca web
        self.search_keywords = [
            "cerca", "trova", "notizie", "ultime", "oggi", "recenti",
            "search", "find", "news", "latest", "look up", "informazioni su"
        ]
    
    async def search_web(self, query: str) -> str:
        """Cerca su DuckDuckGo e formatta i risultati."""
        try:
            results = await self.ddgs.text(query, max_results=5, region="it-it")
            formatted = []
            for i, r in enumerate(results, 1):
                title = r.get('title', 'N/A')
                body = r.get('body', '')[:250]
                formatted.append(f"[{i}] {title}: {body}")
            return "\n".join(formatted)
        except Exception as e:
            logger.error(f"Errore ricerca web: {e}")
            return ""
    
    async def ask_groq(self, user_text: str, web_context: str = "") -> str:
        """Invia la richiesta a Groq con o senza risultati web."""
        messages = []
        
        if web_context:
            messages.append({
                "role": "system",
                "content": (
                    "Sei un assistente AI per dispositivi IoT. "
                    "Usa i risultati web forniti per rispondere in modo aggiornato. "
                    "Rispondi in italiano. Sii conciso (massimo 3 frasi).\n\n"
                    f"Risultati web:\n{web_context}"
                )
            })
        else:
            messages.append({
                "role": "system",
                "content": "Sei un assistente AI per dispositivi IoT. Rispondi in italiano. Sii conciso."
            })
        
        messages.append({"role": "user", "content": user_text})
        
        try:
            response = await self.groq_client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=300,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Errore Groq: {e}")
            return "Mi dispiace, non riesco a rispondere in questo momento."
    
    def needs_search(self, text: str) -> bool:
        """Controlla se l'utente vuole fare una ricerca."""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.search_keywords)
    
    async def handler(self, websocket, path):
        """Gestisce ogni connessione WebSocket da Xiaozhi."""
        client_ip = websocket.remote_address[0]
        logger.info(f"📱 Client connesso: {client_ip}")
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    text = data.get("text", "")
                    logger.info(f"🎤 Ricevuto: {text[:60]}...")
                    
                    # Decidi se cercare sul web
                    if self.needs_search(text):
                        logger.info("🔍 Ricerca web attivata")
                        web_results = await self.search_web(text)
                        answer = await self.ask_groq(text, web_results)
                    else:
                        logger.info("💬 Risposta diretta LLM")
                        answer = await self.ask_groq(text)
                    
                    # Invia risposta a Xiaozhi
                    response = {
                        "type": "tts",
                        "text": answer,
                        "session_id": data.get("session_id", "")
                    }
                    await websocket.send(json.dumps(response))
                    logger.info(f"✅ Risposta inviata: {answer[:60]}...")
                    
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "text": "Formato messaggio non valido"
                    }))
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"❌ Client disconnesso: {client_ip}")
        except Exception as e:
            logger.error(f"Errore: {e}")

async def main():
    server = XiaozhiGroqServer()
    port = int(os.getenv("PORT", 8000))
    
    logger.info(f"🚀 Server Xiaozhi + Groq avviato su porta {port}")
    
    async with websockets.serve(server.handler, "0.0.0.0", port):
        await asyncio.Future()  # Resta in ascolto per sempre

if __name__ == "__main__":
    asyncio.run(main())
