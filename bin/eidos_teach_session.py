#!/usr/bin/env python3
"""
bin/eidos_teach_session.py — Sesión de aprendizaje autónoma SUPERVISADA. S125 (13 jun 2026).

SER se fue 3h y pidió: "enséñale tú todo; si falla, que aprenda y razone sobre ello; procede".
Claude (maestro) deja a EIDOS estudiando un currículo alineado con SER (Linux/seguridad/
virtualización/redes), en tandas pequeñas y PAUSADAS, todo headless / por sus IAs (SIN tocar
ratón ni pantalla — seguro estando SER fuera y al volver). Razona sobre lo aprendido y escribe
~/.eidos/study_report.md. Se detiene en DEADLINE (antes de que SER vuelva).

NO usa: ratón, BOM real, navegador visible, ni sitios que bloquean bots (labex.io da 403).
Lección enseñada al arrancar: por qué labex.io falló y qué hacer en su lugar.
"""
import os
import sys
import time
import logging

sys.path.insert(0, "/home/ser/EIDOS")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("eidos.teach")

DEADLINE = time.time() + int(os.environ.get("SESSION_SECS", "10200"))   # ~2h50m
GAP = int(os.environ.get("SESSION_GAP", "240"))                          # pausa entre tandas

# Currículo del maestro: educativo, alineado con SER (admin Linux, Kali, KVM, redes, CS).
# Mezcla conceptos (van a sus IAs + grafo) y razonamiento (conectar lo aprendido).
CURRICULUM = [
    ("que es systemd y como gestiona servicios en linux", 2),
    ("que es y como funciona iptables", 2),
    ("que es nftables y en que se diferencia de iptables", 3),
    ("que es KVM en linux", 2),
    ("que es QEMU y como se relaciona con KVM", 2),
    ("que es libvirt y para que sirve", 2),
    ("que es un namespace de linux", 3),
    ("que son los cgroups en linux", 3),
    ("que es docker y como usa namespaces y cgroups", 3),
    ("que es kubernetes y para que sirve", 4),
    ("que es nmap y para que se usa", 2),
    ("que es wireshark y para que sirve", 2),
    ("que es tcpdump", 3),
    ("que es el modelo OSI de redes", 3),
    ("que es TCP y como hace el handshake de 3 vias", 3),
    ("que es DNS y como resuelve nombres", 3),
    ("que es una VLAN", 4),
    ("que es SSH y como funciona la autenticacion por clave publica", 2),
    ("que es un certificado TLS y como funciona el cifrado", 3),
    ("que es SELinux y AppArmor", 4),
    ("que es un firewall stateful", 3),
    ("que es Burp Suite y para que se usa en pentesting", 3),
    ("que es Metasploit framework", 3),
    ("que es una inyeccion SQL y como se previene", 3),
    ("que es XSS y como se mitiga", 4),
    ("que es el principio de minimo privilegio", 3),
    ("que es bash scripting y para que sirve", 2),
    ("que es awk y sed en linux", 3),
    ("que es un proceso zombie y uno huerfano en linux", 3),
    ("que es la memoria virtual y el swap en linux", 3),
    ("que es un grafo de conocimiento", 2),
    ("que es el q-learning en aprendizaje por refuerzo", 3),
    ("que es un embedding vectorial", 3),
    ("que es una base de datos vectorial como chromadb", 3),
    ("que es el protocolo MCP (model context protocol)", 3),
    ("que es la cognicion encarnada (embodied cognition)", 4),
    ("razona: como se complementan nmap y wireshark en un analisis de red", 5),
    ("razona: por que KVM+QEMU+libvirt forman una pila de virtualizacion", 5),
    ("razona: que ventajas tiene un grafo de conocimiento frente a un LLM sin memoria", 5),
    ("razona: como se relacionan iptables, namespaces y docker", 5),
]


def teach_labex_lesson():
    """Lección del maestro: por que labex.io fallo y que hacer en su lugar."""
    try:
        from core.eidos_active_research import _persist
        _persist(
            "labex.io bloquea bots (registro autonomo)",
            "labex.io devuelve HTTP 403 a navegadores automatizados/headless y usa captcha, "
            "por eso EIDOS no pudo registrarse solo. Leccion: el registro autonomo headless NO "
            "funciona en sitios anti-bot; requiere el navegador real con la sesion de SER (cookies) "
            "o que SER este presente para resolver el captcha. Alternativa para aprender sin bloqueo: "
            "contenido abierto (man, tldr, wikipedia, docs oficiales) y consultar sus propias IAs.",
            "claude_taught", 0.9)
        log.info("🎓 ensenada la leccion de labex.io (anti-bot)")
    except Exception as e:
        log.warning("no pude persistir leccion labex: %s", e)


def main():
    from core.study_queue import enqueue, list_items, run_pending, write_report
    teach_labex_lesson()
    for directive, prio in CURRICULUM:
        enqueue(directive, prio)
    log.info("📚 currículo en cola: %d tareas. Estudiando hasta DEADLINE.", len(CURRICULUM))

    cycles = 0
    while time.time() < DEADLINE:
        cycles += 1
        pend = list_items("pending")
        if pend:
            try:
                r = run_pending(max_items=1, dry_run=True)
                log.info("ciclo %d: aprendidas=%s falladas=%s pend=%d",
                         cycles, r.get("learned"), r.get("failed"), len(list_items("pending")))
            except Exception as e:
                log.warning("ciclo %d run_pending: %s", cycles, e)
        else:
            # cola vacía → EIDOS busca sus propios huecos y los estudia (sigue aprendiendo solo)
            added = 0
            try:
                from core.autonomous_research_loop import _get_graph_gaps
                for g in (_get_graph_gaps(3) or []):
                    if g and len(g) > 2:
                        enqueue(f"que es {g}", 6)
                        added += 1
            except Exception as e:
                log.debug("graph_gaps: %s", e)
            log.info("ciclo %d: cola vacía, +%d huecos del grafo", cycles, added)
            if added == 0:
                break
        write_report()
        if time.time() + GAP >= DEADLINE:
            break
        time.sleep(GAP)
    write_report()
    log.info("✅ sesión de aprendizaje terminada (%d ciclos). Informe: ~/.eidos/study_report.md", cycles)


if __name__ == "__main__":
    main()
