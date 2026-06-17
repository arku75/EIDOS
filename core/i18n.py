"""
EIDOS core/i18n.py — Internationalization / Multi-Language Support
==================================================================
Soporte multi-idioma para la interfaz de EIDOS.
Idiomas: es (español, default), en (English), pt (Português).

Uso:
    from core.i18n import t, set_lang, get_lang
    set_lang("en")
    print(t("welcome"))  # "Welcome to EIDOS"
    print(t("status.online"))  # "Online"
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("eidos.i18n")

_current_lang: str = os.environ.get("EIDOS_LANG", "es")

# ══════════════════════════════════════════════════════════════════════════════
#  TRANSLATIONS
# ══════════════════════════════════════════════════════════════════════════════

TRANSLATIONS: dict[str, dict[str, str]] = {
    # ── General ──
    "welcome": {
        "es": "Bienvenido a EIDOS — Agente Soberano",
        "en": "Welcome to EIDOS — Sovereign Agent",
        "pt": "Bem-vindo ao EIDOS — Agente Soberano",
    },
    "goodbye": {
        "es": "Hasta pronto, SER.",
        "en": "See you soon, SER.",
        "pt": "Até logo, SER.",
    },
    "thinking": {
        "es": "Pensando...",
        "en": "Thinking...",
        "pt": "Pensando...",
    },
    "error": {
        "es": "Error",
        "en": "Error",
        "pt": "Erro",
    },
    "unknown_command": {
        "es": "Comando desconocido",
        "en": "Unknown command",
        "pt": "Comando desconhecido",
    },

    # ── Status ──
    "status.online": {
        "es": "EN LÍNEA",
        "en": "ONLINE",
        "pt": "ONLINE",
    },
    "status.offline": {
        "es": "DESCONECTADO",
        "en": "OFFLINE",
        "pt": "OFFLINE",
    },
    "status.system": {
        "es": "Estado del Sistema",
        "en": "System Status",
        "pt": "Estado do Sistema",
    },
    "status.modules": {
        "es": "Módulos",
        "en": "Modules",
        "pt": "Módulos",
    },
    "status.health": {
        "es": "Salud del Sistema",
        "en": "System Health",
        "pt": "Saúde do Sistema",
    },

    # ── Autonomy ──
    "auto.mood": {
        "es": "Estado de ánimo",
        "en": "Mood",
        "pt": "Humor",
    },
    "auto.goals": {
        "es": "Objetivos activos",
        "en": "Active goals",
        "pt": "Objetivos ativos",
    },
    "auto.thoughts": {
        "es": "Pensamientos recientes",
        "en": "Recent thoughts",
        "pt": "Pensamentos recentes",
    },
    "auto.actions": {
        "es": "Acciones ejecutadas",
        "en": "Actions executed",
        "pt": "Ações executadas",
    },
    "auto.idle": {
        "es": "Inactivo",
        "en": "Idle",
        "pt": "Inativo",
    },

    # ── Security ──
    "sec.scan_complete": {
        "es": "Escaneo completado",
        "en": "Scan completed",
        "pt": "Varredura completa",
    },
    "sec.alert": {
        "es": "Alerta de seguridad",
        "en": "Security alert",
        "pt": "Alerta de segurança",
    },
    "sec.vuln_found": {
        "es": "Vulnerabilidad encontrada",
        "en": "Vulnerability found",
        "pt": "Vulnerabilidade encontrada",
    },

    # ── Services ──
    "svc.active": {
        "es": "Activo",
        "en": "Active",
        "pt": "Ativo",
    },
    "svc.inactive": {
        "es": "Inactivo",
        "en": "Inactive",
        "pt": "Inativo",
    },
    "svc.failed": {
        "es": "Fallido",
        "en": "Failed",
        "pt": "Falhou",
    },
    "svc.restarting": {
        "es": "Reiniciando",
        "en": "Restarting",
        "pt": "Reiniciando",
    },

    # ── Commands ──
    "cmd.help": {
        "es": "Muestra esta ayuda",
        "en": "Show this help",
        "pt": "Mostra esta ajuda",
    },
    "cmd.status": {
        "es": "Estado completo del sistema",
        "en": "Full system status",
        "pt": "Estado completo do sistema",
    },
    "cmd.goals": {
        "es": "Goals activos y planes",
        "en": "Active goals and plans",
        "pt": "Objetivos ativos e planos",
    },

    # ── Learning ──
    "learn.new_doc": {
        "es": "Nuevo documento aprendido",
        "en": "New document learned",
        "pt": "Novo documento aprendido",
    },
    "learn.new_skill": {
        "es": "Nueva habilidad aprendida",
        "en": "New skill learned",
        "pt": "Nova habilidade aprendida",
    },

    # ── Network ──
    "net.new_host": {
        "es": "Nuevo host detectado",
        "en": "New host detected",
        "pt": "Novo host detectado",
    },
    "net.host_gone": {
        "es": "Host desaparecido",
        "en": "Host gone",
        "pt": "Host desapareceu",
    },
    "net.scanning": {
        "es": "Escaneando red...",
        "en": "Scanning network...",
        "pt": "Escaneando rede...",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def t(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """Translate a key to the current (or specified) language.

    Supports format args: t("hello", name="World") -> "Hello, World"
    """
    lang = lang or _current_lang
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key  # Return key as-is if not found
    text = entry.get(lang) or entry.get("es") or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


def set_lang(lang: str) -> None:
    """Set the current language (es, en, pt)."""
    global _current_lang
    if lang in ("es", "en", "pt"):
        _current_lang = lang
        log.info("Language set to: %s", lang)
    else:
        log.warning("Unsupported language: %s (use es, en, pt)", lang)


def get_lang() -> str:
    """Get current language code."""
    return _current_lang


def available_langs() -> list[str]:
    """List available language codes."""
    return ["es", "en", "pt"]


def get_all_keys() -> list[str]:
    """List all translation keys."""
    return sorted(TRANSLATIONS.keys())


@property
def stats() -> dict:
    return {
        "current_lang": _current_lang,
        "languages": available_langs(),
        "total_keys": len(TRANSLATIONS),
    }


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("EIDOS i18n — Multi-Language Support")
    print(f"  Current: {get_lang()}")
    print(f"  Keys: {len(TRANSLATIONS)}")
    print(f"  Languages: {available_langs()}")

    for lang in available_langs():
        set_lang(lang)
        print(f"\n  [{lang}]")
        for key in ["welcome", "status.online", "auto.goals", "sec.alert", "net.scanning"]:
            print(f"    {key}: {t(key)}")

    set_lang("es")
    print("\nOK")
