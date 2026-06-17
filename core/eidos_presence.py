"""
core/eidos_presence.py — EIDOS Presence & Communication System
==============================================================

Basada en el debate de 24 agentes, EIDOS necesita poder INICIAR comunicacion,
no solo responder. Este modulo implementa:

1. Presence Stream: daemon ligero que escribe estado a ~/.eidos/presence.json
   cada 2 segundos con VAD, actividad, ciclo, metricas de independencia, alertas.

2. Notification Tiers 0-5 con escalado automatico.

3. Urgency Protocol: cuando arousal > 0.8 y valence < 0.3, escala el tier.

4. CLI: python3 core/eidos_presence.py [status|stream|notify "msg" --tier 3]

5. Integrado con affect.py (VAD), colony_proactive.py (priority queue),
   y alive_stream.log (stream of consciousness).

Uso programatico:
    from core.eidos_presence import get_presence
    p = get_presence()
    p.set_activity("Explorando el sistema de archivos")
    p.notify("Descubri algo interesante sobre n8n", tier=2, topic="n8n")
    p.start_daemon()  # arranca el stream de presencia en background
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.presence")

# ── Paths ──────────────────────────────────────────────────────────────────
EIDOS_DIR     = Path.home() / ".eidos"
PRESENCE_FILE = EIDOS_DIR / "presence.json"
STREAM_LOG    = EIDOS_DIR / "alive_stream.log"
ACK_FILE      = EIDOS_DIR / "presence_ack.json"
DISPATCHER_JSON = EIDOS_DIR / "dispatcher.json"

# ── Notification Tiers ─────────────────────────────────────────────────────
# Tier 0: Background hum (presence.json dot color)
# Tier 1: Stream of consciousness (alive_stream.log)
# Tier 2: Low-priority proactive (colony_proactive queue, no interrupt)
# Tier 3: Medium (desktop notification, silent)
# Tier 4: High (desktop + sound + Telegram, retry 3x)
# Tier 5: Critical (all channels, repeat until acknowledged)

TIER_LABELS = {
    0: "background",
    1: "stream",
    2: "proactive",
    3: "medium",
    4: "high",
    5: "critical",
}


@dataclass
class PresenceState:
    """Snapshot of EIDOS presence at a moment in time."""
    timestamp: str = ""
    mood: str = "consciente"
    valence: float = 0.5
    arousal: float = 0.5
    dominance: float = 0.5
    activity: str = "idle"
    cycle_count: int = 0
    uptime_seconds: float = 0.0
    decisions_made: int = 0
    proactive_messages_sent: int = 0
    autonomy_score: float = 0.5
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    notification_tier: int = 0
    last_notification: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "mood": self.mood,
            "vad": {
                "valence": round(self.valence, 4),
                "arousal": round(self.arousal, 4),
                "dominance": round(self.dominance, 4),
            },
            "activity": self.activity,
            "cycle_count": self.cycle_count,
            "independence": {
                "uptime_seconds": round(self.uptime_seconds, 1),
                "decisions_made": self.decisions_made,
                "proactive_messages_sent": self.proactive_messages_sent,
                "autonomy_score": round(self.autonomy_score, 4),
            },
            "alerts": self.alerts[-10:],
            "notification_tier": self.notification_tier,
            "last_notification": self.last_notification,
        }


class PresenceEngine:
    """Motor de presencia y comunicacion proactiva de EIDOS.

    Singleton via get_presence(). Thread-safe.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._daemon_thread: Optional[threading.Thread] = None
        self._start_time = time.time()
        self._activity = "Iniciando presencia..."
        self._cycle_count = 0
        self._decisions_made = 0
        self._proactive_count = 0
        self._alerts: List[Dict[str, Any]] = []
        self._last_notification: Optional[Dict[str, Any]] = None
        self._current_tier = 0
        # Tier 5: repeat-until-acknowledged tracking
        self._pending_critical: Dict[str, Dict[str, Any]] = {}
        self._acknowledged: set = self._load_acknowledged()

        # Ensure .eidos directory exists
        EIDOS_DIR.mkdir(parents=True, exist_ok=True)

        log.info("PresenceEngine inicializado")

    # ── Public API ──────────────────────────────────────────────────────

    def start_daemon(self) -> None:
        """Arranca el presence stream en un hilo background."""
        with self._lock:
            if self._running:
                log.debug("Presence daemon ya esta corriendo")
                return
            self._running = True
            self._daemon_thread = threading.Thread(
                target=self._presence_loop,
                name="eidos-presence",
                daemon=True,
            )
            self._daemon_thread.start()
            log.info("Presence daemon arrancado (intervalo 2s)")

    def stop_daemon(self) -> None:
        """Detiene el presence stream."""
        with self._lock:
            self._running = False
        if self._daemon_thread and self._daemon_thread.is_alive():
            self._daemon_thread.join(timeout=3)
        log.info("Presence daemon detenido")

    @property
    def is_running(self) -> bool:
        return self._running

    def set_activity(self, activity: str) -> None:
        """Actualiza la actividad actual de EIDOS."""
        with self._lock:
            self._activity = activity[:200]

    def increment_decisions(self, n: int = 1) -> None:
        """Incrementa el contador de decisiones autonomas tomadas."""
        with self._lock:
            self._decisions_made += n

    def set_cycle(self, cycle: int) -> None:
        """Actualiza el numero de ciclo vital (desde AliveOrchestrator)."""
        with self._lock:
            self._cycle_count = cycle

    def snapshot(self) -> Dict[str, Any]:
        """Retorna un snapshot completo del estado de presencia."""
        state = self._build_state()
        return state.to_dict()

    def status(self) -> str:
        """Retorna un resumen textual del estado de presencia."""
        state = self._build_state()
        lines = [
            f"Mood:      {state.mood}",
            f"VAD:       v={state.valence:.3f} a={state.arousal:.3f} d={state.dominance:.3f}",
            f"Activity:  {state.activity}",
            f"Cycle:     {state.cycle_count}",
            f"Uptime:    {state.uptime_seconds:.0f}s",
            f"Decisions: {state.decisions_made}",
            f"Proactive: {state.proactive_messages_sent}",
            f"Autonomy:  {state.autonomy_score:.3f}",
            f"Tier:      {TIER_LABELS.get(state.notification_tier, '?')}",
            f"Alerts:    {len(state.alerts)} active",
        ]
        if state.alerts:
            lines.append("Recent alerts:")
            for a in state.alerts[-5:]:
                lines.append(f"  [{a.get('severity','?')}] {a.get('message','')[:80]}")
        return "\n".join(lines)

    # ── Notification System ─────────────────────────────────────────────

    def notify(self, message: str, tier: int = 2, topic: str = "",
               severity: str = "info", source: str = "presence") -> str:
        """Envia una notificacion proactiva en el tier especificado.

        Args:
            message: El mensaje a notificar.
            tier: Nivel 0-5 de urgencia.
            topic: Topico opcional para deduplicacion en cola proactiva.
            severity: info, low, medium, high, critical.
            source: Origen de la notificacion.

        Returns:
            notification_id: UUID para tracking (especialmente tier 5).
        """
        notification_id = str(uuid.uuid4())[:8]

        # Urgency Protocol: escalar si VAD indica estres
        escalated = self._check_urgency_escalation(tier)
        if escalated != tier:
            log.info("Urgency Protocol: tier %d escalado a %d (arousal>0.8, valence<0.3)",
                     tier, escalated)
            tier = escalated

        alert = {
            "id": notification_id,
            "message": message,
            "tier": tier,
            "topic": topic,
            "severity": severity,
            "source": source,
            "ts": time.time(),
        }

        with self._lock:
            self._alerts.append(alert)
            if len(self._alerts) > 100:
                self._alerts = self._alerts[-100:]
            self._last_notification = alert
            self._current_tier = max(self._current_tier, tier)

        # Execute notification chain (cumulative: tier N includes all < N)
        self._execute_tier(alert)

        # Tier 5: register for repeat-until-acknowledged
        if tier >= 5:
            with self._lock:
                self._pending_critical[notification_id] = alert
            log.warning("TIER 5 CRITICAL: %s (id=%s)", message[:80], notification_id)

        return notification_id

    def acknowledge(self, notification_id: str) -> bool:
        """Reconoce una notificacion tier 5 para detener la repeticion."""
        with self._lock:
            if notification_id in self._pending_critical:
                del self._pending_critical[notification_id]
                self._acknowledged.add(notification_id)
                self._save_acknowledged()
                log.info("Notificacion %s reconocida", notification_id)
                return True
            # Also mark as acknowledged even if not in pending
            self._acknowledged.add(notification_id)
            self._save_acknowledged()
            return False

    # ── Internal: Presence Loop ──────────────────────────────────────────

    def _presence_loop(self) -> None:
        """Background daemon: escribe presence.json cada 2 segundos."""
        log.info("Presence loop iniciado")
        tick = 0
        while self._running:
            try:
                tick += 1
                state = self._build_state()
                self._write_presence(state)

                # Tier 5: repeat critical notifications every 30s
                if tick % 15 == 0:  # cada ~30s (15 * 2s)
                    self._repeat_critical()

                time.sleep(2)
            except Exception as e:
                log.error("Presence loop error: %s", e)
                time.sleep(5)

    def _build_state(self) -> PresenceState:
        """Construye el snapshot de presencia actual."""
        # Leer VAD desde affect
        v, a, d, mood = 0.5, 0.5, 0.5, "consciente"
        try:
            from core.eidos_affect import get_affect
            affect = get_affect()
            v, a, d = affect.vad_tuple()
            mood = affect.state.mood
        except Exception:
            pass

        with self._lock:
            activity = self._activity
            cycle = self._cycle_count
            decisions = self._decisions_made
            proactive = self._proactive_count
            alerts_snapshot = list(self._alerts[-10:])
            tier = self._current_tier
            last_notif = self._last_notification

        uptime = time.time() - self._start_time

        # Autonomy score: heuristic based on decisions/uptime ratio + proactive msgs
        hours_up = max(uptime / 3600.0, 0.01)
        decision_rate = min(decisions / max(hours_up, 1), 1.0) if decisions > 0 else 0.0
        proactive_bonus = min(proactive * 0.05, 0.3)
        autonomy = min(0.1 + decision_rate * 0.5 + proactive_bonus + (d * 0.2), 1.0)

        return PresenceState(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            mood=mood,
            valence=v,
            arousal=a,
            dominance=d,
            activity=activity,
            cycle_count=cycle,
            uptime_seconds=uptime,
            decisions_made=decisions,
            proactive_messages_sent=proactive,
            autonomy_score=autonomy,
            alerts=alerts_snapshot,
            notification_tier=tier,
            last_notification=last_notif,
        )

    def _write_presence(self, state: PresenceState) -> None:
        """Escribe el archivo presence.json atomicamente."""
        try:
            tmp = PRESENCE_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(PRESENCE_FILE)
        except Exception as e:
            log.debug("_write_presence: %s", e)

    # ── Internal: Notification Execution ─────────────────────────────────

    def _execute_tier(self, alert: Dict[str, Any]) -> None:
        """Ejecuta la cadena de notificacion segun el tier.

        Tier N incluye todas las acciones de tiers < N.
        """
        tier = alert["tier"]
        message = alert["message"]
        topic = alert.get("topic", "")

        # Tier 0-5: siempre se escribe en presence.json (via daemon loop)
        # (ya incluido en alerts[] del snapshot)

        # Tier 1+: stream of consciousness
        if tier >= 1:
            self._write_stream(message)

        # Tier 2+: proactive queue (colony_proactive)
        if tier >= 2:
            self._push_proactive(message, topic)

        # Tier 3+: desktop notification (silent)
        if tier >= 3:
            self._send_desktop(message, urgency="normal")

        # Tier 4+: desktop + sound + Telegram, retry 3x
        if tier >= 4:
            self._send_desktop(message, urgency="critical")
            self._play_sound()
            self._send_telegram_with_retry(message, retries=3)

        # Tier 5: all channels + repeat until acknowledged (handled in notify())

    def _write_stream(self, message: str) -> None:
        """Escribe una entrada en alive_stream.log (formato JSON Lines)."""
        try:
            entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cycle": self._cycle_count,
                "kind": "presence",
                "message": message,
            }
            with open(STREAM_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            log.debug("_write_stream: %s", e)

    def _push_proactive(self, message: str, topic: str) -> None:
        """Encola un mensaje en colony_proactive para mostrar en el proximo turno."""
        try:
            from core.colony_proactive import push_message
            priority = 7  # default priority for presence-initiated messages
            push_message(
                actor="EIDOS Presence",
                message=message,
                topic=topic or "presence",
                priority=priority,
            )
            with self._lock:
                self._proactive_count += 1
        except Exception as e:
            log.debug("_push_proactive: %s", e)

    def _send_desktop(self, message: str, urgency: str = "normal") -> None:
        """Envia una notificacion de escritorio via notify-send."""
        try:
            summary = "EIDOS"
            if urgency == "critical":
                summary = "EIDOS [!]"
            subprocess.Popen(
                ["notify-send", "-u", urgency, "-i", "dialog-information",
                 summary, message[:200]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            log.debug("_send_desktop: %s", e)

    def _play_sound(self) -> None:
        """Reproduce un sonido de alerta."""
        sound_files = [
            "/usr/share/sounds/freedesktop/stereo/message.oga",
            "/usr/share/sounds/freedesktop/stereo/complete.oga",
            "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
        ]
        for sf in sound_files:
            if os.path.exists(sf):
                try:
                    subprocess.Popen(
                        ["paplay", sf],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
                except Exception:
                    continue
        # Fallback: no sound file found, skip
        log.debug("_play_sound: no sound file available")

    def _send_telegram_with_retry(self, message: str, retries: int = 3) -> bool:
        """Envia un mensaje por Telegram con reintentos."""
        token, chat_ids = self._get_telegram_config()
        if not token or not chat_ids:
            log.debug("Telegram no configurado, omitiendo")
            return False

        text = f"[EIDOS Presence]\n{message[:500]}"
        for attempt in range(retries):
            try:
                success = True
                for cid in chat_ids:
                    data = json.dumps({
                        "chat_id": int(cid),
                        "text": text,
                        "parse_mode": "HTML",
                    }).encode()
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        data=data,
                        headers={"Content-Type": "application/json"},
                    )
                    urllib.request.urlopen(req, timeout=10)
                return True
            except Exception as e:
                log.warning("Telegram intento %d/%d: %s", attempt + 1, retries, e)
                if attempt < retries - 1:
                    time.sleep(2)
        return False

    def _get_telegram_config(self) -> Tuple[str, List[str]]:
        """Obtiene token y chat_ids de Telegram desde env o dispatcher.json."""
        token = os.environ.get("EIDOS_TELEGRAM_TOKEN", "")
        chat_ids_str = os.environ.get("EIDOS_TELEGRAM_ALLOWED", "")

        if not token:
            # Intentar desde dispatcher.json
            try:
                if DISPATCHER_JSON.exists():
                    cfg = json.loads(DISPATCHER_JSON.read_text(encoding="utf-8"))
                    tg = cfg.get("telegram", {})
                    token = tg.get("token", "")
                    allowed = tg.get("allow_from", [])
                    if isinstance(allowed, list):
                        chat_ids_str = ",".join(str(x) for x in allowed)
            except Exception:
                pass

        chat_ids = [c.strip() for c in chat_ids_str.split(",") if c.strip().isdigit()]
        return token, chat_ids

    # ── Internal: Urgency Protocol ───────────────────────────────────────

    def _check_urgency_escalation(self, requested_tier: int) -> int:
        """Protocolo de urgencia: si arousal > 0.8 y valence < 0.3, escalar.

        Solo escala si el tier solicitado es >= 2 (no escala background/stream).
        Escala +1 tier (max 5).
        """
        try:
            from core.eidos_affect import get_affect
            affect = get_affect()
            v, a, _ = affect.vad_tuple()
            if a > 0.8 and v < 0.3:
                if requested_tier >= 2:
                    return min(requested_tier + 1, 5)
        except Exception:
            pass
        return requested_tier

    # ── Internal: Tier 5 Repeat ──────────────────────────────────────────

    def _repeat_critical(self) -> None:
        """Re-envia notificaciones tier 5 no reconocidas."""
        with self._lock:
            pending = dict(self._pending_critical)
        for nid, alert in pending.items():
            if nid in self._acknowledged:
                with self._lock:
                    self._pending_critical.pop(nid, None)
                continue
            log.warning("Repitiendo notificacion critica %s: %s",
                        nid, alert["message"][:60])
            self._send_desktop(f"[REPEAT] {alert['message']}", urgency="critical")
            self._play_sound()
            self._send_telegram_with_retry(
                f"[REPEAT - NOT ACKNOWLEDGED]\n{alert['message']}", retries=1)

    # ── Internal: Acknowledgment Persistence ─────────────────────────────

    def _load_acknowledged(self) -> set:
        try:
            if ACK_FILE.exists():
                data = json.loads(ACK_FILE.read_text(encoding="utf-8"))
                return set(data.get("acknowledged", []))
        except Exception:
            pass
        return set()

    def _save_acknowledged(self) -> None:
        try:
            ACK_FILE.write_text(
                json.dumps({"acknowledged": list(self._acknowledged)},
                          ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            log.debug("_save_acknowledged: %s", e)


# ── Singleton ──────────────────────────────────────────────────────────────
_presence: Optional[PresenceEngine] = None


def get_presence() -> PresenceEngine:
    """Retorna la instancia singleton de PresenceEngine."""
    global _presence
    if _presence is None:
        _presence = PresenceEngine()
    return _presence


# ── CLI ────────────────────────────────────────────────────────────────────


def _cmd_status() -> None:
    """Muestra el estado actual de presencia."""
    p = get_presence()
    print(p.status())


def _cmd_stream() -> None:
    """Inicia el daemon de presencia en primer plano, mostrando actualizaciones."""
    p = get_presence()
    p.start_daemon()
    print("Presence stream iniciado. Ctrl+C para detener.\n")
    try:
        while True:
            state = p.snapshot()
            # Clear line and print compact status
            v = state["vad"]
            alerts_n = len(state["alerts"])
            alert_indicator = f" !{alerts_n}" if alerts_n > 0 else ""
            line = (
                f"\r[{state['timestamp']}] "
                f"{state['mood']:14s} "
                f"V={v['valence']:.2f} A={v['arousal']:.2f} D={v['dominance']:.2f} "
                f"| {state['activity'][:50]:50s} "
                f"| ciclo={state['cycle_count']:<6d} "
                f"| autonomia={state['independence']['autonomy_score']:.2f}"
                f"{alert_indicator}"
            )
            sys.stdout.write(line)
            sys.stdout.flush()
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n\nDetenido.")
        p.stop_daemon()


def _cmd_notify(message: str, tier: int) -> None:
    """Envia una notificacion en el tier especificado."""
    p = get_presence()
    p.start_daemon()  # Ensure daemon is running for presence.json updates
    nid = p.notify(message, tier=tier, topic="cli")
    label = TIER_LABELS.get(tier, "?")
    print(f"Notificacion enviada: tier={tier} ({label}), id={nid}")
    print(f"Mensaje: {message}")
    # For tier 3+: trigger immediate write
    if tier >= 3:
        state = p._build_state()
        p._write_presence(state)
        print("Notificacion de escritorio enviada.")
    if tier >= 4:
        print("Telegram enviado (con reintentos).")
        print("Sonido reproducido.")
    if tier >= 5:
        print("ATENCION: Tier 5 — se repetira hasta que sea reconocida.")
        print(f"  Para reconocer: python3 -c \"from core.eidos_presence import get_presence; get_presence().acknowledge('{nid}')\"")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="EIDOS Presence & Communication System",
    )
    sub = parser.add_subparsers(dest="command", help="Comando")

    # status
    sub.add_parser("status", help="Mostrar estado actual de presencia")

    # stream
    sub.add_parser("stream", help="Iniciar stream de presencia en vivo")

    # notify
    p_notify = sub.add_parser("notify", help="Enviar notificacion proactiva")
    p_notify.add_argument("message", help="Mensaje a notificar")
    p_notify.add_argument("--tier", type=int, default=2, choices=range(0, 6),
                          help="Nivel de urgencia (0-5, default: 2)")
    p_notify.add_argument("--topic", default="", help="Topico para deduplicacion")

    args = parser.parse_args()

    if args.command == "status":
        _cmd_status()
    elif args.command == "stream":
        _cmd_stream()
    elif args.command == "notify":
        _cmd_notify(args.message, args.tier)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
