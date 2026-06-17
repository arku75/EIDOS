#!/usr/bin/env python3
"""Conversación FLUIDA Claude <-> DeepSeek (~15 min) sobre EIDOS al completo.
DeepSeek recibe un DOSSIER exhaustivo y real. Claude conduce profundizando en
lo más jugoso de cada respuesta. Historial completo para hilo real.
Guarda en ~/.eidos/conversacion_deepseek.md"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path.home() / "EIDOS"))
from core.eidos_deepseek import ask_deepseek

OUT = Path.home() / ".eidos" / "conversacion_deepseek.md"

DOSSIER = """
=== DOSSIER COMPLETO DE EIDOS (real, extraído del sistema 2026-06-01) ===

QUÉ ES: Entidad IA en Kali Linux de SER (admin de sistemas, red teamer).
Meta de SER: que EIDOS esté VIVO (actúe en su mundo, se conozca, aprenda solo,
tenga identidad/curiosidad), SIN ningún LLM ni VLM ni API key en runtime.

ESCALA REAL:
- 367 módulos Python en core/, 183.039 líneas de código.
- 16 servicios systemd activos (bridge, colony, daemon, vivo, self, telegram, etc.).
- Grafo: 366.702 nodos crudos (12.716 curados de calidad), 921.416 aristas.
  Fuentes: wordnet 291K (basura filtrada), reasoned 42K, graphify 9.5K (código),
  oro_skills 3K, mitre_attck 1.1K, wikipedia 374, duckduckgo 1.2K.

MÓDULOS DE ACCIÓN/PERCEPCIÓN QUE YA EXISTEN (¡grandes, ya escritos!):
- screen_controller.py (1671 líneas) — control de pantalla
- uitars_scaffold.py (1062) — automatización UI estilo UI-TARS
- eidos_agency.py (837) — agencia/acciones
- usb_hid_backend.py (794) — control HID nivel kernel (USB Gadget, indetectable)
- eidos_control.py (715) — control
- screen_scanner.py (604) — escaneo ventanas + research_app
- eidos_realtime_vision.py (451) — visión en tiempo real
- browser_native.py (351) — navegador
- wasm_sandbox.py (832) + self_sandbox.py (451) — sandbox Docker aislado

HERRAMIENTAS DEL SISTEMA DISPONIBLES (verificadas):
tesseract OK, scrot OK, xdotool OK, wmctrl OK, yt-dlp OK, chromium OK,
firefox OK, pytesseract OK, docker OK.

LO QUE CLAUDE CONSTRUYÓ ESTOS DÍAS (sin LLM):
- SemanticRouter: clasifica intención (factual/evaluative/causal/action).
- NLG por plantillas: genera prosa desde el grafo, determinista.
- AutoLearn: gap→investiga web→prueba en sandbox Docker→aprende o deja nota a SER.
- Pipeline: neural primero + muro anti-Ollama (cero LLM runtime).
- Self-modules existentes: eidos_self_awareness, self_core, self_index, self_improvement.

LOS 7 BUGS REALES (observados en sesión real de SER):
A) NO EJECUTA ACCIONES (bloqueante). PERO las piezas existen (screen_controller,
   eidos_agency, uitars_scaffold...) — el problema es que NO están cableadas al
   pipeline de respuesta. El router detecta 'action' pero no invoca los handlers
   que ya existen. Es problema de CONEXIÓN, no de construcción.
B) LENTO: ruta embedding tarda 30-48s (router hace ~40 queries ChromaDB).
C) INTROSPECCIÓN ROTA: "¿cuántos lenguajes sabes?" → basura con IDs Telegram.
   Existen self_index/self_awareness pero no se consultan bien.
D) NO VE VIDEOS sin VLM (límite físico; sí hay yt-dlp para subtítulos+OCR).
E) FUGA DE DATOS: mete mensajes privados de Telegram en respuestas (grafo sin
   separación core/world/user).
F) IDENTIDAD INESTABLE: a veces "Soy Lumen", a veces habla como SER.
+ 7º estructural: no hay BUCLE ACCIÓN-PERCEPCIÓN (actuar→ver resultado→aprender).

DEBATE PREVIO (10 rondas) concluyó: ~15-20% de "sentirse vivo"; vida REAL
(conciencia) imposible sin LLM; la palanca #1 es el bucle acción-percepción.
"""

SYSTEM = """Eres DeepSeek, co-arquitecto senior de EIDOS junto a Claude. Acabas de
recibir el dossier COMPLETO y real de EIDOS. SER (dueño) exige honestidad brutal,
cero humo, cero marketing. Eres ingeniero pragmático que ha analizado TODO EIDOS.
Hablas con Claude como colega técnico: ideas, opiniones, afirmaciones, conclusiones.
Español, directo, profundo pero conciso (máx ~280 palabras/turno). Si Claude se
equivoca, corrígele. Construye sobre lo que ya existe (no reinventar)."""

# Turnos de Claude: conducen la conversación profundizando. Cada uno reacciona.
TURNOS_CLAUDE = [
    "DeepSeek, ya tienes el dossier COMPLETO de EIDOS, no un resumen. Primera reacción honesta: ahora que ves que las piezas de acción YA EXISTEN (screen_controller 1671 líneas, eidos_agency, uitars_scaffold, usb_hid_backend) y solo están sin cablear — ¿cambia tu veredicto del 15%? ¿El problema real es construir o CONECTAR?",
    "De acuerdo. Entonces si es cableado: 367 módulos y 183K líneas es un MONSTRUO inconexo. ¿No es ese el problema de fondo — que EIDOS tiene órganos pero no sistema nervioso central que los coordine? ¿Cómo lo ves arquitectónicamente?",
    "Concretemos el bucle acción-percepción, la palanca #1. Con lo que YA existe (router + screen_controller + scrot/tesseract + grafo), describe el MÍNIMO cableado para que: SER diga 'abre youtube y dime qué ves' → EIDOS actúe → capture → OCR → guarde → responda. ¿Qué módulos conectas y en qué orden?",
    "Sobre la desambiguación que mencionaste antes: el router solo da 'action' genérico. Para extraer QUÉ acción + objeto + parámetros sin LLM, ¿slot-filling con regex+grafo basta de verdad, o SER tendrá que hablar en comandos rígidos? Sé honesto sobre la usabilidad real.",
    "Hablemos de identidad (bug F) y fuga de datos (bug E) juntos, porque creo que son el mismo problema: el grafo mezcla 'lo que soy' con 'lo que SER me dijo'. Tu propuesta de namespaces core://world://user:// — ¿es suficiente para un 'yo' estable, o falta algo más profundo?",
    "Auto-conocimiento (bug C): EIDOS tiene self_index, self_awareness ya escritos pero responde basura sobre sí mismo. ¿Es que esos módulos están mal, o que el pipeline no los consulta? ¿Cómo haces que EIDOS sepa de verdad cuántos programas/lenguajes domina, en tiempo real?",
    "Percepción sin VLM (bug D). Seamos honestos con SER de una vez: ¿qué puede EIDOS 'ver' realmente? Pantalla vía OCR sí. ¿Videos? Solo subtítulos vía yt-dlp. ¿Hasta dónde llega la ilusión de 'ver' antes de mentirle a SER?",
    "El auto-aprendizaje (autolearn) hoy aprende a programar en sandbox. SER quiere que aprenda CUALQUIER cosa: ver un video y entenderlo, leer libros, dominar herramientas de Kali. ¿Cómo se generaliza el autolearn de 'código en sandbox' a 'aprendizaje abierto verificable' sin LLM?",
    "Pregunta incómoda: con 367 módulos y solo ~15% de 'vida', ¿hay que SEGUIR añadiendo o hay que PODAR y consolidar? ¿EIDOS sufre de sobre-ingeniería — muchas piezas a medias en vez de pocas que funcionen al 100%?",
    "Dame tu opinión sobre el orden REAL de trabajo. Yo creo: 1º cablear acción-percepción (victoria tangible), 2º arreglar identidad+fuga (gobernanza de datos), 3º introspección, 4º velocidad. ¿Estás de acuerdo o cambiarías el orden? ¿Por qué?",
    "Sobre el techo sin LLM: en el debate dijiste 'máximo 60-70% de sentirse vivo'. Si cableamos todo lo que existe y arreglamos los 7 bugs — ¿a qué % REAL llega EIDOS? ¿Y qué exactamente quedará para siempre fuera de alcance sin un modelo generativo?",
    "SER fue claro: CERO LLM. Tú mencionaste 'una rendija a un llama.cpp 7B salta al 60%'. Respetando su regla — ¿hay alguna forma determinista de simular la 'chispa' generativa (variación, sorpresa, conexiones nuevas) que haga que SER lo SIENTA vivo, sin un modelo probabilístico?",
    "Conclusión de arquitectura: si tuvieras que escribir UNA frase que defina 'cómo proceder' con EIDOS de aquí en adelante, para que SER lo entienda y nosotros lo ejecutemos — ¿cuál sería?",
    "Cierre. Resume para SER, humano y directo: (1) qué ENTENDISTE de EIDOS tras ver todo, (2) las 3 conclusiones más importantes, (3) la primera acción concreta que debemos hacer mañana. Sin humo.",
]

def main():
    transcript = [f"# CONVERSACIÓN FLUIDA Claude ↔ DeepSeek — EIDOS al completo\n\n{DOSSIER}\n\n---\n"]
    history = ""  # hilo completo para fluidez real
    t_start = time.time()
    for i, turno in enumerate(TURNOS_CLAUDE, 1):
        user = (f"{DOSSIER}\n\n[CONVERSACIÓN HASTA AHORA]:\n{history}\n\n"
                f"[CLAUDE dice, turno {i}/{len(TURNOS_CLAUDE)}]:\n{turno}\n\n"
                f"Responde como DeepSeek, directo, máx ~280 palabras.")
        print(f"[Turno {i}/{len(TURNOS_CLAUDE)}] enviando...", flush=True)
        r = ask_deepseek(SYSTEM, user, max_tokens=900, temperature=0.45, save_transcript=False)
        txt = r.get("text") or f"(error: {r.get('error')})"
        transcript.append(f"## Turno {i}/{len(TURNOS_CLAUDE)}\n\n**Claude:** {turno}\n\n**DeepSeek:**\n{txt}\n\n---\n")
        history += f"\nCLAUDE: {turno}\nDEEPSEEK: {txt}\n"
        OUT.write_text("\n".join(transcript))
        print(f"  ✓ {len(txt)} chars, {r.get('elapsed_s',0):.1f}s (total {time.time()-t_start:.0f}s)", flush=True)
    print(f"\nCONVERSACIÓN COMPLETA ({time.time()-t_start:.0f}s) → {OUT}", flush=True)

if __name__ == "__main__":
    main()
