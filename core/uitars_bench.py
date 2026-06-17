"""
core/uitars_bench.py — Benchmark honesto de modelos para el scaffold
=====================================================================
Mide, por modelo: tiempo total, nº pasos, éxito, y la respuesta final.
Tarea fija reproducible (shell determinista) para comparar manzanas
con manzanas. Usado por la sesión autónoma S57d para elegir el modelo
pequeño <7B que mejor calidad+rendimiento da SIN GPU.

Uso:
    EIDOS_TEXT_URL=http://localhost:11435 \\
    python3 core/uitars_bench.py lfm2.5-1.2b-instruct:q4_0 llama3.2:1b lfm2.5-thinking:1.2b
"""
from __future__ import annotations
import os, sys, time, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tarea con respuesta verificable: el modelo debe ejecutar `echo` y
# reportar el texto exacto. Permite puntuar CALIDAD objetivamente.
TASK = ("ejecuta exactamente el comando: echo EIDOS_OK_42 ; "
        "luego dime en finished() el texto que devolvió y termina")
EXPECT = "EIDOS_OK_42"


def bench(model: str) -> dict:
    os.environ["EIDOS_TEXT_MODEL"] = model
    from importlib import reload
    import core.uitars_scaffold as us
    reload(us)
    t0 = time.time()
    try:
        r = us.UITarsScaffold(mode="text").run(TASK, max_steps=4)
        dt = round(time.time() - t0, 1)
        ran_echo = any(s.action_type == "shell" and "echo" in
                       json.dumps(s.action_inputs) for s in r.steps)
        got_expect = EXPECT in (r.answer or "") or any(
            EXPECT in s.observation for s in r.steps)
        quality = ("alta" if (r.success and got_expect) else
                   "media" if got_expect or ran_echo else "baja")
        return {"model": model, "ok": r.success, "status": r.status.value,
                "steps": len(r.steps), "secs": dt, "exec_echo": ran_echo,
                "got_expected": got_expect, "quality": quality,
                "answer": (r.answer or "")[:120]}
    except Exception as e:  # noqa: BLE001
        return {"model": model, "ok": False, "status": "exception",
                "steps": 0, "secs": round(time.time() - t0, 1),
                "exec_echo": False, "got_expected": False,
                "quality": "fallo", "answer": f"EXC: {e}"[:120]}


if __name__ == "__main__":
    models = sys.argv[1:] or ["lfm2.5-1.2b-instruct:q4_0", "llama3.2:1b", "lfm2.5-thinking:1.2b"]
    url = os.environ.get("EIDOS_TEXT_URL", "(auto)")
    print(f"BENCH scaffold | url={url} | tarea verificable (echo {EXPECT})\n")
    rows = []
    for m in models:
        print(f"→ probando {m} ...", flush=True)
        r = bench(m)
        rows.append(r)
        print(f"   {r['quality']:6s} ok={r['ok']} {r['secs']}s "
              f"pasos={r['steps']} echo={r['exec_echo']} "
              f"exp={r['got_expected']} :: {r['answer'][:70]}\n", flush=True)
    # Ranking: calidad > velocidad
    qrank = {"alta": 0, "media": 1, "baja": 2, "fallo": 3}
    rows.sort(key=lambda x: (qrank.get(x["quality"], 9), x["secs"]))
    print("=== RANKING (calidad, luego velocidad) ===")
    for i, r in enumerate(rows, 1):
        print(f"{i}. {r['model']:16s} {r['quality']:6s} "
              f"{r['secs']:6.1f}s ok={r['ok']} steps={r['steps']}")
    print(f"\n🏆 GANADOR: {rows[0]['model']} "
          f"(calidad={rows[0]['quality']}, {rows[0]['secs']}s)")
    print("JSON:", json.dumps(rows, ensure_ascii=False))
