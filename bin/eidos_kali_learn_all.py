#!/usr/bin/env python3
"""
bin/eidos_kali_learn_all.py — EIDOS aprende TODAS las apps de Kali (no solo unas pocas). S125.

SER: "en Kali hay muchas más apps, no solo las que dijiste; que aprenda el todo".
Enumera TODOS los .desktop del menú, saca Name/Comment/Categories/binario, y aprende cada app
a su grafo (descripción del .desktop + whatis del binario). RÁPIDO: local, SQLite directo (no
reconstruye el grafo de 33k → carga baja). Complementa el estudio EN PROFUNDIDAD (vía IAs) que
hace la cola para las herramientas clave, y el night_study que ya barrió los binarios.
"""
import glob
import os
import subprocess
import sys
from pathlib import Path
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.db import get_conn

DB = os.path.expanduser("~/.eidos/evolution_brain.db")


def parse_desktop(path):
    name = comment = cats = exec_ = ""
    try:
        for line in open(path, encoding="utf-8", errors="ignore"):
            s = line.strip()
            if s.startswith("Name=") and not name:
                name = s[5:]
            elif s.startswith("Comment=") and not comment:
                comment = s[8:]
            elif s.startswith("Categories=") and not cats:
                cats = s[11:].strip(";").replace(";", ", ")
            elif s.startswith("Exec=") and not exec_:
                tok = s[5:].split()
                if tok:
                    exec_ = tok[0].split("/")[-1]
    except Exception:
        pass
    return name, comment, cats, exec_


def whatis(binary):
    if not binary:
        return ""
    try:
        out = subprocess.run(["whatis", binary], capture_output=True, text=True, timeout=4).stdout.strip()
        if out and "nothing appropriate" not in out:
            return out.split(" - ", 1)[-1][:200]
    except Exception:
        pass
    return ""


def main():
    c = get_conn(DB, timeout=30)
    files = sorted(glob.glob("/usr/share/applications/*.desktop"))
    learned = skipped = 0
    for f in files:
        name, comment, cats, exec_ = parse_desktop(f)
        if not name or len(name) < 2:
            continue
        concept = f"app kali: {name}"
        if c.execute("SELECT 1 FROM knowledge_nodes WHERE concept=?", (concept,)).fetchone():
            skipped += 1
            continue
        defn = comment or whatis(exec_) or f"Aplicación de Kali."
        if exec_ and exec_.lower() not in defn.lower():
            defn = f"{defn} (binario: {exec_})"
        if cats:
            defn = f"{defn}. Categorías: {cats}"
        c.execute(
            "INSERT INTO knowledge_nodes "
            "(id, concept, definition, category, confidence, source, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("kapp_" + uuid.uuid4().hex[:12], concept, defn[:600], "kali_app",
             0.75, "kali_app_scan", time.strftime("%Y-%m-%d %H:%M:%S")))
        learned += 1
        if learned % 50 == 0:
            c.commit()
    c.commit()
    print(f"apps Kali aprendidas: {learned} | ya conocidas: {skipped} | total .desktop: {len(files)}")


if __name__ == "__main__":
    main()
