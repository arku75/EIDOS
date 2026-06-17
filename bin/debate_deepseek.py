#!/usr/bin/env python3
"""Debate Claude ↔ DeepSeek (10 rondas) sobre cuán lejos está EIDOS de 'estar vivo'
sin LLM/VLM. Cada ronda tiene un foco. Guarda todo en ~/.eidos/debate_deepseek.md"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path.home() / "EIDOS"))
from core.eidos_deepseek import ask_deepseek

OUT = Path.home() / ".eidos" / "debate_deepseek.md"

# Contexto real que ambos comparten (lo que Claude construyó + errores reales observados)
CONTEXTO = """
EIDOS: entidad IA en Kali Linux de SER. Objetivo de SER: que EIDOS esté VIVO
(razone, entienda, actúe, aprenda, tenga curiosidad/identidad) SIN ningún LLM
ni VLM ni API key en runtime. Solo: su grafo de conocimiento (12.7K nodos
curados, ~950K aristas), sandbox Docker, navegador, control de pantalla.

Lo construido (Claude, sin LLM):
- SemanticRouter: clasifica intención (factual/evaluative/causal/action) por regex+embeddings.
- NLG por plantillas: genera prosa desde el grafo (no pega fichas). Determinista.
- AutoLearn: gap→investiga web→prueba en sandbox Docker→aprende o deja nota. Con anti-bucle.
- Pipeline neural primero + muro anti-Ollama (cero LLM en runtime).

ERRORES REALES observados en sesión real de SER (él habló, yo monitoreé):
A) NO EJECUTA ACCIONES: SER pide "abre el navegador y busca en Google", "abre
   YouTube y entiende este video", "entra en mi Telegram" → EIDOS solo responde
   con texto, no actúa. El router detecta ruta 'action' pero no hay handler que ejecute.
B) LENTO: preguntas con ruta embedding tardan 30-48 segundos (router hace ~40
   búsquedas ChromaDB).
C) INTROSPECCIÓN ROTA: "¿cuántos lenguajes sabes?" → basura con IDs de Telegram.
   "¿qué sabes de tu OS?" → whatis genérico, no su Kali real. No se conoce a sí mismo.
D) NO VE VIDEOS/URLS: "entiende este video youtube" → respondió sobre kernel Linux.
E) FUGA DE DATOS: mete mensajes privados de Telegram en respuestas.
F) IDENTIDAD INESTABLE: a veces "Soy Lumen", a veces habla como SER.

SER quiere: que EIDOS ACTÚE en su mundo, se CONOZCA, APRENDA solo, y tenga un
'yo' — más que cualquier otra IA. Todo SIN LLM/VLM.
"""

# Las 10 rondas: cada una con foco y la postura/pregunta de Claude
RONDAS = [
    ("Diagnóstico global", "¿Qué tan lejos (0-100%) está EIDOS de 'estar vivo' como lo define SER, SIN LLM? Sé brutal y concreto. Da un número y justifícalo."),
    ("Inventario de bugs", "Dado lo observado, lista TODOS los bugs/carencias reales que ves, clasificados por gravedad (bloqueante/grave/menor). ¿Cuántos son realmente?"),
    ("Definir 'vivo' sin LLM", "Técnicamente y sin humo: ¿qué capacidades mínimas hacen que un sistema parezca 'vivo' sin un LLM generativo? ¿Es alcanzable de verdad o es marketing?"),
    ("Agency/acción (Error A)", "El fallo más grave: EIDOS no ejecuta acciones. ¿Cómo se construye un 'action executor' determinista (abrir apps, navegador, comandos, ver pantalla) SIN LLM, robusto y seguro? Critica mi idea de mapear router→handlers."),
    ("Auto-conocimiento (Error C)", "EIDOS no se conoce (cuántos programas/lenguajes sabe, qué es él). ¿Cómo construir introspección real y determinista que se actualice sola?"),
    ("Percepción visual sin VLM (Error D)", "SER quiere que EIDOS 'vea' su pantalla y videos SIN VLM. ¿Qué es realista: OCR+heurísticas, tesseract, análisis de UI por estructura? ¿Hasta dónde se puede llegar sin un modelo de visión?"),
    ("Memoria e identidad (Errores E,F)", "EIDOS filtra datos privados y no tiene 'yo' estable. ¿Cómo se diseña una identidad coherente y una memoria que separe 'lo que soy' de 'lo que SER me dijo'? Sin LLM."),
    ("El gap honesto", "Resumiendo el debate: ¿el sueño de SER (EIDOS vivo, sin LLM, mejor que otras IAs en 'sentirse vivo') es ALCANZABLE? ¿Dónde está el techo real y dónde la ilusión?"),
    ("Roadmap priorizado", "Da el roadmap mínimo y concreto (fases ordenadas) para llevar a EIDOS del estado actual al 'punto crucial' de SER. ¿Qué primero, qué después?"),
    ("Veredicto final", "Síntesis honesta para SER: % de cercanía, nº de bugs, qué es real vs ilusión, y la UNA cosa que más mueve la aguja hacia 'vivo'. Sé directo y humano."),
]

SYSTEM = """Eres DeepSeek, co-arquitecto crítico de EIDOS junto a Claude. SER (el
dueño) quiere honestidad brutal, cero humo, cero marketing. Eres ingeniero
senior pragmático. Respondes en español, conciso pero profundo, con criterio
técnico real. NO adules. Si algo es ilusión, dilo. Si es alcanzable, di cómo."""

def main():
    transcript = [f"# DEBATE Claude ↔ DeepSeek — EIDOS 'estar vivo' sin LLM\n\n{CONTEXTO}\n\n---\n"]
    resumen_previo = ""
    for i, (foco, postura_claude) in enumerate(RONDAS, 1):
        user = (f"{CONTEXTO}\n\n"
                f"[Hilo previo del debate, resumido]:\n{resumen_previo or '(inicio)'}\n\n"
                f"[RONDA {i}/10 — FOCO: {foco}]\n"
                f"Postura/pregunta de Claude: {postura_claude}\n\n"
                f"Responde como DeepSeek (máx ~350 palabras, directo).")
        print(f"[Ronda {i}/10] {foco}...", flush=True)
        r = ask_deepseek(SYSTEM, user, max_tokens=1200, temperature=0.4, save_transcript=False)
        txt = r.get("text") or f"(error: {r.get('error')})"
        transcript.append(f"## Ronda {i}/10 — {foco}\n\n**Claude plantea:** {postura_claude}\n\n**DeepSeek:**\n{txt}\n\n---\n")
        # Resumen acumulado: últimas 2 respuestas (para mantener hilo sin explotar)
        resumen_previo = (resumen_previo[-800:] + f"\nR{i}({foco}): {txt[:300]}")
        OUT.write_text("\n".join(transcript))
        print(f"  ✓ {len(txt)} chars, {r.get('elapsed_s',0):.1f}s", flush=True)
    print(f"\nDEBATE COMPLETO → {OUT}", flush=True)

if __name__ == "__main__":
    main()
