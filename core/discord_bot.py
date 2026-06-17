"""
EIDOS core/discord_bot.py — Discord Bot Interface
===================================================
Bot de Discord para EIDOS con comandos de estado,
goals, thoughts, shell y chat libre via Ollama.

Configuración:
    export EIDOS_DISCORD_TOKEN="tu_bot_token"
    export EIDOS_DISCORD_CHANNEL="canal_id"  # Canal permitido (opcional)

Uso:
    python core/discord_bot.py              # Standalone
    from core.discord_bot import start_discord_bot
    await start_discord_bot()               # Integrado
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import urllib.request
from typing import Optional

log = logging.getLogger("eidos.discord")

# Safe shell prefixes (same as autonomous.py)
SAFE_PREFIXES = [
    "uptime", "whoami", "hostname", "uname", "date", "df", "free",
    "ip a", "ip addr", "ip route", "ss -tlnp", "ps aux",
    "systemctl status", "systemctl is-active", "docker ps",
    "nmap", "dig", "host", "whois", "curl -s", "ping -c",
    "cat /etc/os-release", "cat /proc/cpuinfo", "cat /proc/meminfo",
    "ls", "wc", "head", "tail", "find", "which",
]


def _safe_shell(cmd: str, timeout: int = 15) -> str:
    """Run a safe shell command."""
    cmd = cmd.strip()
    if not any(cmd.startswith(p) for p in SAFE_PREFIXES):
        return f"[BLOCKED] Command not in safe list: {cmd.split()[0]}"
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        out = (r.stdout + r.stderr).strip()
        return out[:1800] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "(timeout)"
    except Exception as e:
        return f"(error: {e})"


def _get_system_status() -> str:
    """Get system status summary."""
    lines = ["**EIDOS System Status**\n"]

    # Ollama
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.load(resp)
            models = [m["name"] for m in data.get("models", [])]
            lines.append(f"🟢 Ollama: {len(models)} models")
    except Exception:
        lines.append("🔴 Ollama: OFFLINE")

    # RAM
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f.readlines()[:5]:
                k, v = line.split(":")
                mem[k.strip()] = int(v.strip().split()[0])
            total = mem.get("MemTotal", 1)
            avail = mem.get("MemAvailable", 0)
            pct = (1 - avail / max(total, 1)) * 100
            lines.append(f"{'🟢' if pct < 75 else '🟡'} RAM: {pct:.1f}%")
    except Exception:
        pass  # error no crítico, continuar
    # Uptime
    try:
        r = subprocess.run(["uptime", "-p"], capture_output=True, text=True, timeout=5)
        lines.append(f"⏱️ {r.stdout.strip()}")
    except Exception:
        pass  # error no crítico, continuar
    # Health
    try:
        from core.system_health import get_health_monitor
        hm = get_health_monitor()
        lines.append(f"💊 {hm.get_summary_line()}")
    except Exception:
        pass  # error no crítico, continuar
    return "\n".join(lines)


def _ollama_quick(prompt: str, model: str = "deepseek-r1:14b") -> str:
    """Quick Ollama query."""
    try:
        payload = json.dumps({
            "model": model,
            "prompt": f"[INST] Responde breve (max 300 chars): {prompt} [/INST]",
            "stream": False,
            "options": {"num_predict": 200, "temperature": 0.7, "num_ctx": 512},
        }).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
            return data.get("response", "").strip()[:1500]
    except Exception as e:
        return f"(Ollama error: {e})"


# ══════════════════════════════════════════════════════════════════════════════
#  DISCORD BOT
# ══════════════════════════════════════════════════════════════════════════════

_bot_instance = None


async def start_discord_bot():
    """Start the Discord bot (non-blocking)."""
    global _bot_instance

    token = os.environ.get("EIDOS_DISCORD_TOKEN", "")
    if not token:
        log.warning("EIDOS_DISCORD_TOKEN not set — Discord bot disabled")
        return

    try:
        import discord
        from discord.ext import commands
    except ImportError:
        log.error("discord.py not installed: pip install discord.py")
        return

    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix="!", intents=intents)
    allowed_channel = os.environ.get("EIDOS_DISCORD_CHANNEL", "")

    def _check_channel(ctx) -> bool:
        if not allowed_channel:
            return True
        return str(ctx.channel.id) == allowed_channel

    @bot.event
    async def on_ready():
        log.info(f"Discord bot connected as {bot.user}")

    @bot.command(name="status")
    async def cmd_status(ctx):
        if not _check_channel(ctx):
            return
        status = _get_system_status()
        await ctx.send(status)

    @bot.command(name="goals")
    async def cmd_goals(ctx):
        if not _check_channel(ctx):
            return
        try:
            from core.autonomous import get_autonomous
            core = get_autonomous()
            goals = core.get_active_goals(5)
            if goals:
                lines = ["**Active Goals:**\n"]
                for g in goals:
                    lines.append(f"• [{g.priority}] {g.title}")
                await ctx.send("\n".join(lines))
            else:
                await ctx.send("No active goals.")
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @bot.command(name="thoughts")
    async def cmd_thoughts(ctx):
        if not _check_channel(ctx):
            return
        try:
            from core.autonomous import get_autonomous
            core = get_autonomous()
            thoughts = core.get_recent_thoughts(5)
            if thoughts:
                lines = ["**Recent Thoughts:**\n"]
                for t in thoughts:
                    lines.append(f"• {t[:150]}")
                await ctx.send("\n".join(lines))
            else:
                await ctx.send("No thoughts yet.")
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @bot.command(name="health")
    async def cmd_health(ctx):
        if not _check_channel(ctx):
            return
        try:
            from core.system_health import get_health_monitor
            hm = get_health_monitor()
            summary = hm.get_summary_line()
            alerts = hm.get_alerts()
            msg = f"**Health:** {summary}"
            if alerts:
                msg += "\n**Alerts:**\n"
                for a in alerts[:5]:
                    msg += f"• {a['message']}\n"
            await ctx.send(msg)
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @bot.command(name="shell")
    async def cmd_shell(ctx, *, cmd: str = ""):
        if not _check_channel(ctx):
            return
        if not cmd:
            await ctx.send("Usage: `!shell <command>`")
            return
        result = _safe_shell(cmd)
        # Discord max message is 2000 chars
        if len(result) > 1900:
            result = result[:1900] + "\n...(truncated)"
        await ctx.send(f"```\n{result}\n```")

    @bot.command(name="alerts")
    async def cmd_alerts(ctx):
        if not _check_channel(ctx):
            return
        try:
            from core.alert_manager import get_alert_manager
            am = get_alert_manager()
            recent = am.get_recent(10)
            if recent:
                lines = ["**Recent Alerts:**\n"]
                sev_icon = {"info": "ℹ️", "low": "🔵", "medium": "🟡", "high": "🟠", "critical": "🔴"}
                for a in recent:
                    icon = sev_icon.get(a['severity'], '❓')
                    lines.append(f"{icon} [{a['source']}] {a['title']}")
                await ctx.send("\n".join(lines))
            else:
                await ctx.send("No alerts.")
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @bot.command(name="prioritize")
    async def cmd_prioritize(ctx):
        if not _check_channel(ctx):
            return
        try:
            from core.autonomous import get_autonomous
            from core.smart_prioritizer import get_prioritizer
            core = get_autonomous()
            goals = core.get_active_goals(10)
            if not goals:
                await ctx.send("No active goals to prioritize.")
                return
            sp = get_prioritizer()
            ranked = sp.prioritize(goals)
            lines = ["**Smart Priority Ranking:**\n"]
            for i, s in enumerate(ranked, 1):
                bar = "█" * int(s.total * 10) + "░" * (10 - int(s.total * 10))
                lines.append(f"{i}. `{s.total:.3f}` {bar} {s.goal_title}")
            await ctx.send("\n".join(lines))
        except Exception as e:
            await ctx.send(f"Error: {e}")

    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            return
        if allowed_channel and str(message.channel.id) != allowed_channel:
            return

        # Process commands first
        await bot.process_commands(message)

        # Free chat (if not a command)
        if not message.content.startswith("!"):
            # Only respond if bot is mentioned or in DM
            if bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
                clean = message.content.replace(f"<@{bot.user.id}>", "").strip()
                if clean:
                    async with message.channel.typing():
                        response = await asyncio.get_event_loop().run_in_executor(
                            None, _ollama_quick, clean
                        )
                    await message.channel.send(response or "(no response)")

    _bot_instance = bot
    log.info("Starting Discord bot...")
    await bot.start(token)


def get_bot_status() -> dict:
    """Get Discord bot status."""
    token = os.environ.get("EIDOS_DISCORD_TOKEN", "")
    return {
        "configured": bool(token),
        "connected": _bot_instance is not None and _bot_instance.is_ready() if _bot_instance else False,
        "commands": ["!status", "!goals", "!thoughts", "!health", "!shell", "!alerts", "!prioritize"],
    }


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Discord Bot — Status Check")
    s = get_bot_status()
    print(f"  Configured: {s['configured']}")
    print(f"  Connected: {s['connected']}")
    print(f"  Commands: {', '.join(s['commands'])}")
    print(f"  Shell test: {_safe_shell('uptime')}")
    print(f"  Status test: {_get_system_status()[:200]}")
    token = os.environ.get("EIDOS_DISCORD_TOKEN", "")
    if token:
        print("\n  Token found, would start bot...")
    else:
        print("\n  Set EIDOS_DISCORD_TOKEN to enable")
    print("OK")
