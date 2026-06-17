"""
EIDOS core/claude_web.py — DOM Agent para Claude.ai via Playwright
===================================================================
Abre Claude.ai en un Chromium visible, envía el prompt y extrae la respuesta.
Reutiliza sesión guardada en ~/.eidos/chrome_profile_claude (persiste cookies/login).

Uso:
    from core.claude_web import ask_claude_sync
    resp = ask_claude_sync("Explica qué es Docker en 3 líneas")

Requisitos:
    pip install playwright && playwright install chromium
"""
from __future__ import annotations

import asyncio
import os
import re

HAS_PLAYWRIGHT = False
try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    pass

USER_DATA_DIR = os.path.expanduser("~/.eidos/chrome_profile_claude")


async def ask_claude_web(prompt: str, timeout: int = 90000) -> str:
    """
    Abre Claude.ai, pega el prompt y devuelve la respuesta como texto plano.
    Si no está logueado, deja la ventana abierta para que el usuario inicie sesión.
    """
    if not HAS_PLAYWRIGHT:
        return (
            "[ORACLE] Playwright no instalado.\n"
            "Ejecuta: pip install playwright && playwright install chromium"
        )

    os.makedirs(USER_DATA_DIR, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            args=["--start-maximized", "--no-first-run"],
            viewport=None,
        )

        page = await browser.new_page()
        try:
            await page.goto("https://claude.ai/new", timeout=30000)

            # Espera el input (si no aparece → necesita login)
            input_sel = 'div[contenteditable="true"][data-placeholder]'
            fallback_sel = 'div[contenteditable="true"]'

            try:
                await page.wait_for_selector(input_sel, timeout=12000)
                sel = input_sel
            except Exception:
                try:
                    await page.wait_for_selector(fallback_sel, timeout=8000)
                    sel = fallback_sel
                except Exception:
                    await asyncio.sleep(30)          # Dar tiempo para login manual
                    try:
                        await page.wait_for_selector(fallback_sel, timeout=20000)
                        sel = fallback_sel
                    except Exception:
                        return "⚠️ Necesitas iniciar sesión en Claude. Abre el navegador manualmente."

            # Inyectar texto via JS para evitar problemas con caracteres especiales
            escaped = prompt.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
            await page.evaluate(f"""
                (() => {{
                    const el = document.querySelector('{sel}');
                    if (!el) return;
                    el.focus();
                    // Limpiar contenido previo
                    el.innerHTML = '';
                    // Insertar texto
                    document.execCommand('insertText', false, `{escaped}`);
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }})()
            """)
            await asyncio.sleep(0.8)

            # Enviar (Enter o botón submit)
            send_btn = 'button[aria-label*="Send"], button[data-testid="send-button"]'
            try:
                btn = page.locator(send_btn)
                if await btn.count() > 0:
                    await btn.first.click()
                else:
                    await page.keyboard.press("Enter")
            except Exception:
                await page.keyboard.press("Enter")

            # Esperar a que termine de generar
            # Señal de fin: botón de Stop desaparece O botón Copy aparece
            await asyncio.sleep(3)

            stop_sel = 'button[aria-label*="Stop"]'
            try:
                # Si hay botón Stop, esperar a que desaparezca
                if await page.locator(stop_sel).count() > 0:
                    await page.locator(stop_sel).wait_for(state="hidden", timeout=timeout)
            except Exception:
                pass  # error no crítico, continuar
            await asyncio.sleep(1.5)

            # Extraer la última respuesta del asistente
            # Claude usa diferentes selectores según versión
            selectors_to_try = [
                '.font-claude-message',
                '[data-testid="assistant-message"]',
                '.prose',
                'div[class*="message"][class*="assistant"]',
            ]

            result = ""
            for selector in selectors_to_try:
                blocks = await page.locator(selector).all_inner_texts()
                if blocks:
                    result = blocks[-1].strip()
                    break

            if not result:
                # Último recurso: extraer todo el texto visible de la conversación
                result = await page.evaluate("""
                    Array.from(document.querySelectorAll('.font-claude-message, [data-testid="assistant-message"], .prose'))
                        .map(el => el.innerText)
                        .pop() || '(No se pudo extraer la respuesta)'
                """)

            return result.strip() or "(Sin respuesta visible)"

        except Exception as e:
            return f"❌ Error claude_web: {e}"
        finally:
            try:
                await browser.close()
            except Exception:
                pass  # error no crítico, continuar
def ask_claude_sync(prompt: str, timeout: int = 90000) -> str:
    """Wrapper síncrono para usar desde el REPL o tools.py."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Si ya hay un loop corriendo (ej.: eidos_tui), usar nest_asyncio o thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as ex:
                future = ex.submit(asyncio.run, ask_claude_web(prompt, timeout))
                return future.result(timeout=timeout // 1000 + 10)
        else:
            return loop.run_until_complete(ask_claude_web(prompt, timeout))
    except Exception as e:
        return f"❌ ask_claude_sync error: {e}"


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "Explica el concepto de heartbeat en arquitecturas de agentes IA en 3 líneas"  # pyre-ignore[arg-type]
    print(f"🧠 Preguntando a Claude: {q[:80]}...")  # pyre-ignore[arg-type]
    resp = ask_claude_sync(q)
    print("\n━━━ RESPUESTA DE CLAUDE ━━━")
    print(resp)
