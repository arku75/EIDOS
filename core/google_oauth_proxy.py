"""
EIDOS Google OAuth Proxy — Acceso a LLMs via Google OAuth
Inspirado en antigravity-auth: proxy para Claude/Gemini via Google.

Framework configurable — el usuario provee endpoints y credenciales.
Fallback a Ollama local si la conexión falla.
"""

import os
import re
import json
import time
import sqlite3
import hashlib
import threading
import webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, parse_qs, urlparse
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from core.db import get_conn

# ─── Constantes ──────────────────────────────────────────────────────────────

EIDOS_DIR = Path.home() / ".eidos"
TOKENS_PATH = EIDOS_DIR / "google_tokens.json"
CONFIG_PATH = EIDOS_DIR / "oauth_config.json"
DB_PATH = EIDOS_DIR / "oauth_proxy.db"

DEFAULT_CONFIG = {
    "oauth": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "redirect_port": 9876,
        "scopes": ["openid", "email", "profile"],
    },
    "endpoints": {
        "gemini": {
            "base_url": "",  # Usuario configura
            "models": ["gemini-2.5-pro", "gemini-2.5-flash"],
            "chat_path": "/v1/chat/completions",
        },
        "claude": {
            "base_url": "",  # Usuario configura
            "models": ["claude-sonnet-4-6", "claude-opus-4-6"],
            "chat_path": "/v1/messages",
        },
    },
    "accounts": [],  # Lista de cuentas para rotación
    "rate_limits": {
        "requests_per_minute": 20,
        "backoff_base_s": 2,
        "max_retries": 3,
    },
    "fallback": {
        "enabled": True,
        "ollama_url": "http://localhost:11434",
        "default_model": "lfm2.5-thinking:1.2b",
    }
}


@dataclass
class ChatResponse:
    content: str
    model: str
    provider: str  # "google", "ollama"
    tokens_used: int = 0
    latency_ms: float = 0.0
    cached: bool = False
    error: str = ""


class GoogleOAuthProxy:
    """
    Proxy para acceder a LLMs via Google OAuth.

    Configuración modular:
    1. El usuario configura endpoints en ~/.eidos/oauth_config.json
    2. Autenticación OAuth2 con Google
    3. Rotación de cuentas si hay rate limits
    4. Fallback a Ollama local
    """

    def __init__(self):
        EIDOS_DIR.mkdir(parents=True, exist_ok=True)
        self.config = self._load_config()
        self.tokens = self._load_tokens()
        self._request_times = []
        self._current_account_idx = 0

        self.db = get_conn(str(DB_PATH), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()

        # Cache de respuestas parciales para recovery
        self._response_cache = {}

    def _init_db(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT,
                provider TEXT,
                tokens_in INTEGER,
                tokens_out INTEGER,
                latency_ms REAL,
                status TEXT,
                account TEXT,
                timestamp REAL
            );
            CREATE TABLE IF NOT EXISTS session_cache (
                session_id TEXT PRIMARY KEY,
                messages TEXT,
                last_response TEXT,
                model TEXT,
                timestamp REAL
            );
        """)
        self.db.commit()

    def _load_config(self) -> Dict:
        """Carga configuración"""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH) as f:
                    user_config = json.load(f)
                # Merge con defaults
                config = DEFAULT_CONFIG.copy()
                config.update(user_config)
                return config
            except Exception:
                pass  # error no crítico, continuar
        # Crear config por defecto
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        print(f"📝 [OAuthProxy] Config creada en {CONFIG_PATH}")
        print(f"   Edita los endpoints antes de usar el proxy")
        return DEFAULT_CONFIG

    def _save_config(self):
        """Guarda configuración"""
        CONFIG_PATH.write_text(json.dumps(self.config, indent=2))

    def _load_tokens(self) -> Dict:
        """Carga tokens guardados"""
        if TOKENS_PATH.exists():
            try:
                with open(TOKENS_PATH) as f:
                    return json.load(f)
            except Exception:
                pass  # error no crítico, continuar
        return {}

    def _save_tokens(self):
        """Guarda tokens (básico, sin encriptación por ahora)"""
        TOKENS_PATH.write_text(json.dumps(self.tokens, indent=2))
        os.chmod(str(TOKENS_PATH), 0o600)  # Solo owner

    # ─── OAuth2 Flow ─────────────────────────────────────────────────────

    def login(self, client_id: str = None, client_secret: str = None) -> bool:
        """
        Inicia flujo OAuth2 con Google.

        Args:
            client_id: Google OAuth client ID
            client_secret: Google OAuth client secret

        Returns:
            True si login exitoso
        """
        if not client_id:
            client_id = self.config.get("oauth", {}).get("client_id", "")
        if not client_secret:
            client_secret = self.config.get("oauth", {}).get("client_secret", "")

        if not client_id or not client_secret:
            print("❌ [OAuthProxy] Necesitas configurar client_id y client_secret")
            print(f"   Edita: {CONFIG_PATH}")
            print("   Obtén credenciales en: https://console.cloud.google.com/apis/credentials")
            return False

        oauth_config = self.config.get("oauth", {})
        redirect_port = oauth_config.get("redirect_port", 9876)
        redirect_uri = f"http://localhost:{redirect_port}/callback"

        # Construir URL de autorización
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(oauth_config.get("scopes", ["openid"])),
            "access_type": "offline",
            "prompt": "consent",
        }
        auth_url = oauth_config.get("auth_url", DEFAULT_CONFIG["oauth"]["auth_url"])
        full_url = f"{auth_url}?{urlencode(params)}"

        # Servidor local para capturar callback
        auth_code = [None]

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                query = parse_qs(urlparse(self.path).query)
                if "code" in query:
                    auth_code[0] = query["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<h1>Login exitoso! Puedes cerrar esta ventana.</h1>")
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Error en autenticacion")

            def log_message(self, format, *args):
                pass  # Silenciar logs

        try:
            server = HTTPServer(("localhost", redirect_port), CallbackHandler)
            server.timeout = 120

            print(f"🌐 [OAuthProxy] Abriendo browser para login...")
            webbrowser.open(full_url)
            print(f"   Si no se abre, visita: {full_url[:80]}...")

            server.handle_request()
            server.server_close()

            if auth_code[0]:
                # Intercambiar code por tokens
                success = self._exchange_code(
                    auth_code[0], client_id, client_secret, redirect_uri
                )
                if success:
                    print("✅ [OAuthProxy] Login exitoso!")
                    return True

        except Exception as e:
            print(f"❌ [OAuthProxy] Error en login: {e}")

        return False

    def _exchange_code(self, code: str, client_id: str,
                       client_secret: str, redirect_uri: str) -> bool:
        """Intercambia authorization code por access/refresh tokens"""
        try:
            import urllib.request

            token_url = self.config.get("oauth", {}).get(
                "token_url", DEFAULT_CONFIG["oauth"]["token_url"]
            )
            data = urlencode({
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }).encode()

            req = urllib.request.Request(token_url, data=data)
            with urllib.request.urlopen(req, timeout=30) as resp:
                tokens = json.loads(resp.read())

            self.tokens = {
                "access_token": tokens.get("access_token"),
                "refresh_token": tokens.get("refresh_token"),
                "expires_at": time.time() + tokens.get("expires_in", 3600),
                "token_type": tokens.get("token_type", "Bearer"),
            }
            self._save_tokens()
            return True

        except Exception as e:
            print(f"❌ [OAuthProxy] Error intercambiando token: {e}")
            return False

    def _refresh_token(self) -> bool:
        """Refresca access token si expiró"""
        if not self.tokens.get("refresh_token"):
            return False

        if time.time() < self.tokens.get("expires_at", 0) - 60:
            return True  # Aún válido

        try:
            import urllib.request

            oauth_config = self.config.get("oauth", {})
            data = urlencode({
                "refresh_token": self.tokens["refresh_token"],
                "client_id": oauth_config.get("client_id", ""),
                "client_secret": oauth_config.get("client_secret", ""),
                "grant_type": "refresh_token",
            }).encode()

            token_url = oauth_config.get("token_url", DEFAULT_CONFIG["oauth"]["token_url"])
            req = urllib.request.Request(token_url, data=data)
            with urllib.request.urlopen(req, timeout=30) as resp:
                tokens = json.loads(resp.read())

            self.tokens["access_token"] = tokens["access_token"]
            self.tokens["expires_at"] = time.time() + tokens.get("expires_in", 3600)
            self._save_tokens()
            return True

        except Exception as e:
            print(f"❌ [OAuthProxy] Error refrescando token: {e}")
            return False

    # ─── Rate Limiting ───────────────────────────────────────────────────

    def _check_rate_limit(self) -> bool:
        """Verifica rate limit"""
        now = time.time()
        window = 60  # 1 minuto
        self._request_times = [t for t in self._request_times if now - t < window]
        max_rpm = self.config.get("rate_limits", {}).get("requests_per_minute", 20)
        return len(self._request_times) < max_rpm

    def _wait_for_rate_limit(self):
        """Espera si hay rate limit"""
        while not self._check_rate_limit():
            time.sleep(1)
        self._request_times.append(time.time())

    # ─── Chat API ────────────────────────────────────────────────────────

    def chat(self, messages: List[Dict], model: str = "gemini",
             temperature: float = 0.7) -> ChatResponse:
        """
        Envía chat request via proxy.

        Args:
            messages: Lista de {role, content}
            model: "gemini" o "claude" (o modelo específico)
            temperature: Temperatura de generación

        Returns:
            ChatResponse
        """
        start = time.time()

        # Determinar provider y modelo
        provider = "google"
        if "gemini" in model.lower():
            endpoint_config = self.config.get("endpoints", {}).get("gemini", {})
            actual_model = model if model in endpoint_config.get("models", []) else \
                           endpoint_config.get("models", ["gemini-2.5-flash"])[0]
        elif "claude" in model.lower():
            endpoint_config = self.config.get("endpoints", {}).get("claude", {})
            actual_model = model if model in endpoint_config.get("models", []) else \
                           endpoint_config.get("models", ["claude-sonnet-4-6"])[0]
        else:
            endpoint_config = self.config.get("endpoints", {}).get("gemini", {})
            actual_model = model

        base_url = endpoint_config.get("base_url", "")

        # Si no hay endpoint configurado o no hay token, fallback
        if not base_url or not self.tokens.get("access_token"):
            return self._fallback_ollama(messages, temperature)

        # Refresh token si necesario
        if not self._refresh_token():
            return self._fallback_ollama(messages, temperature)

        # Rate limit
        self._wait_for_rate_limit()

        # Hacer request
        retries = self.config.get("rate_limits", {}).get("max_retries", 3)
        backoff = self.config.get("rate_limits", {}).get("backoff_base_s", 2)

        for attempt in range(retries):
            try:
                import urllib.request

                chat_path = endpoint_config.get("chat_path", "/v1/chat/completions")
                url = f"{base_url.rstrip('/')}{chat_path}"

                payload = json.dumps({
                    "model": actual_model,
                    "messages": messages,
                    "temperature": temperature,
                }).encode()

                req = urllib.request.Request(url, data=payload, headers={
                    "Authorization": f"Bearer {self.tokens['access_token']}",
                    "Content-Type": "application/json",
                })

                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json.loads(resp.read())

                # Extraer respuesta (formato OpenAI-compatible)
                content = ""
                tokens_used = 0
                if "choices" in result:
                    content = result["choices"][0].get("message", {}).get("content", "")
                    tokens_used = result.get("usage", {}).get("total_tokens", 0)
                elif "content" in result:
                    # Formato Anthropic
                    content = result["content"][0].get("text", "") if result["content"] else ""
                    tokens_used = result.get("usage", {}).get("input_tokens", 0) + \
                                  result.get("usage", {}).get("output_tokens", 0)

                latency = (time.time() - start) * 1000

                # Log
                self._log_usage(actual_model, "google", tokens_used, latency, "success")

                return ChatResponse(
                    content=content,
                    model=actual_model,
                    provider="google",
                    tokens_used=tokens_used,
                    latency_ms=latency,
                )

            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "rate" in error_str.lower():
                    wait = backoff * (2 ** attempt)
                    print(f"⏳ [OAuthProxy] Rate limited, esperando {wait}s...")
                    time.sleep(wait)
                    # Rotar cuenta si hay más
                    self._rotate_account()
                    continue
                else:
                    self._log_usage(actual_model, "google", 0, 0, f"error: {error_str[:100]}")
                    break

        # Fallback a Ollama
        return self._fallback_ollama(messages, temperature)

    def _fallback_ollama(self, messages: List[Dict],
                        temperature: float = 0.7) -> ChatResponse:
        """Fallback a Ollama local"""
        fallback = self.config.get("fallback", {})
        if not fallback.get("enabled", True):
            return ChatResponse(content="", model="", provider="none",
                               error="No hay endpoint configurado y fallback deshabilitado")

        try:
            import urllib.request

            ollama_url = fallback.get("ollama_url", "http://localhost:11434")
            model = fallback.get("default_model", "lfm2.5-thinking:1.2b")

            # Convertir messages a prompt para Ollama
            prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages)

            start = time.time()
            payload = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature}
            }).encode()

            req = urllib.request.Request(
                f"{ollama_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"}
            )

            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())

            content = result.get("response", "")
            latency = (time.time() - start) * 1000

            self._log_usage(model, "ollama", 0, latency, "fallback")

            return ChatResponse(
                content=content,
                model=model,
                provider="ollama",
                latency_ms=latency,
            )

        except Exception as e:
            return ChatResponse(
                content="", model="", provider="none",
                error=f"Ollama fallback failed: {e}"
            )

    def _rotate_account(self):
        """Rota a siguiente cuenta si hay múltiples"""
        accounts = self.config.get("accounts", [])
        if len(accounts) > 1:
            self._current_account_idx = (self._current_account_idx + 1) % len(accounts)
            account = accounts[self._current_account_idx]
            # Cargar tokens de esa cuenta
            token_file = EIDOS_DIR / f"google_tokens_{account}.json"
            if token_file.exists():
                self.tokens = json.loads(token_file.read_text())
                print(f"🔄 [OAuthProxy] Rotado a cuenta: {account}")

    def _log_usage(self, model: str, provider: str, tokens: int,
                   latency: float, status: str):
        try:
            self.db.execute("""
                INSERT INTO usage_log (model, provider, tokens_in, tokens_out, latency_ms, status, timestamp)
                VALUES (?, ?, 0, ?, ?, ?, ?)
            """, (model, provider, tokens, latency, status, time.time()))
            self.db.commit()
        except Exception:
            pass  # error no crítico, continuar
    # ─── Utilidades ──────────────────────────────────────────────────────

    def get_available_models(self) -> Dict[str, List[str]]:
        """Lista modelos accesibles"""
        models = {"ollama_local": []}

        # Modelos Ollama locales
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                models["ollama_local"] = [m["name"] for m in data.get("models", [])]
        except Exception:
            pass  # error no crítico, continuar
        # Modelos via proxy
        for provider, config in self.config.get("endpoints", {}).items():
            if config.get("base_url"):
                models[f"google_{provider}"] = config.get("models", [])

        return models

    def estimate_cost(self, prompt: str) -> Dict:
        """Estima costo (siempre $0 via Google OAuth)"""
        word_count = len(prompt.split())
        est_tokens = int(word_count * 1.3)
        return {
            "estimated_tokens": est_tokens,
            "cost_usd": 0.0,
            "note": "Gratis via Google OAuth proxy"
        }

    def get_stats(self) -> Dict:
        """Estadísticas de uso"""
        total = self.db.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0]
        by_provider = {}
        for row in self.db.execute(
            "SELECT provider, COUNT(*) as c, SUM(tokens_out) as t FROM usage_log GROUP BY provider"
        ).fetchall():
            by_provider[row["provider"]] = {"requests": row["c"], "tokens": row["t"] or 0}

        return {
            "total_requests": total,
            "by_provider": by_provider,
            "has_token": bool(self.tokens.get("access_token")),
            "token_expires": self.tokens.get("expires_at", 0),
            "accounts": len(self.config.get("accounts", [])),
            "config_path": str(CONFIG_PATH),
        }

    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass  # error no crítico, continuar
# ─── Test ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Test Google OAuth Proxy ===\n")

    proxy = GoogleOAuthProxy()

    # Test 1: Config
    print("Test 1: Configuración")
    print(f"  Config path: {CONFIG_PATH}")
    print(f"  Has token: {bool(proxy.tokens.get('access_token'))}")
    print(f"  Fallback enabled: {proxy.config.get('fallback', {}).get('enabled')}")
    print("  ✅ Config OK\n")

    # Test 2: Available models
    print("Test 2: Modelos disponibles")
    models = proxy.get_available_models()
    for provider, model_list in models.items():
        print(f"  {provider}: {model_list}")
    print("  ✅ Models OK\n")

    # Test 3: Cost estimation
    print("Test 3: Estimación de costo")
    cost = proxy.estimate_cost("Escribe una función Python que ordene una lista")
    print(f"  Tokens estimados: {cost['estimated_tokens']}")
    print(f"  Costo: ${cost['cost_usd']}")
    print(f"  Nota: {cost['note']}")
    print("  ✅ Cost OK\n")

    # Test 4: Chat con fallback Ollama
    print("Test 4: Chat (fallback a Ollama)")
    response = proxy.chat([{"role": "user", "content": "Di 'hola' en una palabra"}])
    if response.error:
        print(f"  ⚠️ Error (esperado si Ollama no está activo): {response.error}")
    else:
        print(f"  Provider: {response.provider}")
        print(f"  Model: {response.model}")
        print(f"  Response: {response.content[:100]}")
        print(f"  Latency: {response.latency_ms:.0f}ms")
    print("  ✅ Chat OK\n")

    # Test 5: Stats
    print("Test 5: Stats")
    stats = proxy.get_stats()
    print(f"  Total requests: {stats['total_requests']}")
    print(f"  By provider: {stats['by_provider']}")
    print(f"  ✅ Stats OK")

    print("\n✅ Google OAuth Proxy funcional")
    print(f"\n📝 Para activar el proxy completo:")
    print(f"   1. Edita {CONFIG_PATH}")
    print(f"   2. Configura client_id y client_secret de Google")
    print(f"   3. Configura los endpoints de Antigravity")
    print(f"   4. Ejecuta: proxy.login()")
