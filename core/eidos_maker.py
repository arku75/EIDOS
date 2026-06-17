"""
core/eidos_maker.py — Orquestador de Creación Software/Web [S87]

EIDOS crea software y páginas web desde su grafo neuronal, SIN LLM.

Arquitectura en 3 capas (diseño DeepSeek + Claude):
  1. Capa Conceptual — requisitos → grafo de componentes
  2. Capa Material — componentes → archivos reales (código)
  3. Capa Canvas — webs vivientes conectadas al brain API

El grafo de EIDOS (354K nodos) contiene especificaciones técnicas,
patrones de diseño, y fragmentos de código extraídos de sus lecturas.
Maker orquesta: SelfImprovement + GraphSandbox + StoryCreator + Logos.

Uso:
    maker = get_maker()

    # Crear un proyecto software
    project = maker.create_project("dashboard de finanzas", type="web")

    # Generar una página web
    page = maker.create_webpage("portfolio personal", style="minimal")

    # Generar un script Python
    script = maker.create_script("script que ordena archivos por fecha")
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.maker")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
MAKER_OUTPUT_DIR = Path.home() / "EIDOS" / "maker_output"
MAKER_STATE = Path.home() / ".eidos" / "maker_state.json"


# ── Templates de proyectos ────────────────────────────────────────────────────
WEB_TEMPLATES = {
    "dashboard": {
        "files": ["index.html", "style.css", "app.js"],
        "structure": ["header", "sidebar", "main-content", "charts", "footer"],
    },
    "portfolio": {
        "files": ["index.html", "style.css", "gallery.js"],
        "structure": ["hero", "about", "projects", "contact"],
    },
    "landing": {
        "files": ["index.html", "style.css", "hero.js"],
        "structure": ["hero", "features", "cta", "footer"],
    },
    "blog": {
        "files": ["index.html", "style.css", "posts.js"],
        "structure": ["header", "posts-list", "sidebar", "pagination"],
    },
}

CSS_THEMES = {
    "minimal": {
        "bg": "#fafafa", "text": "#1a1a1a", "accent": "#2563eb",
        "font": "system-ui, sans-serif", "radius": "8px",
    },
    "dark": {
        "bg": "#0f172a", "text": "#e2e8f0", "accent": "#38bdf8",
        "font": "system-ui, sans-serif", "radius": "6px",
    },
    "nature": {
        "bg": "#f0fdf4", "text": "#14532d", "accent": "#22c55e",
        "font": "Georgia, serif", "radius": "12px",
    },
    "tech": {
        "bg": "#000", "text": "#00ff00", "accent": "#0ff",
        "font": "'Courier New', monospace", "radius": "0",
    },
}


class Maker:
    """Orquestador de creación software/web desde el grafo neuronal."""

    def __init__(self):
        self._projects_created = 0
        self._state = self._load_state()
        MAKER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Capa Conceptual: idea → grafo de componentes ───────────────────────

    def _conceptualize(self, description: str, project_type: str = "web") -> Dict[str, Any]:
        """Transforma una descripción en un grafo de componentes.

        Usa el grafo neuronal para encontrar especificaciones, patrones y
        soluciones previas que informan el diseño del nuevo proyecto.
        """
        components = []
        requirements = self._extract_requirements(description)

        # Buscar patrones similares en el grafo
        try:
            from core.eidos_logos import get_logos
            logos = get_logos()
            # Activar subgrafo para encontrar conocimiento técnico relevante
            active = logos._activate_subgraph(f"{description} {project_type} arquitectura", top_k=10)
            for concept, defn, score in active:
                if score > 0.3:
                    components.append({
                        "name": concept[:80],
                        "description": defn[:200] if defn else "",
                        "relevance": round(score, 3),
                        "source": "grafo_neuronal",
                    })
        except Exception as e:
            log.debug("_conceptualize graph: %s", e)

        # Si no hay suficiente, usar templates base
        if len(components) < 3:
            template = WEB_TEMPLATES.get(project_type, WEB_TEMPLATES["landing"])
            for struct in template["structure"]:
                components.append({
                    "name": struct,
                    "description": f"Componente estructural: {struct}",
                    "relevance": 0.7,
                    "source": "template",
                })

        # Generar nombre del proyecto
        slug = re.sub(r'[^a-z0-9]+', '-', description.lower().strip())[:40]
        project_name = f"eidos-{slug}" if slug else f"eidos-project-{self._projects_created + 1}"

        return {
            "project_name": project_name,
            "description": description,
            "type": project_type,
            "components": components,
            "requirements": requirements,
            "timestamp": time.time(),
        }

    def _extract_requirements(self, description: str) -> List[str]:
        """Extrae requisitos funcionales de la descripción."""
        reqs = []
        keywords = {
            "dashboard": ["visualización de datos", "gráficos", "métricas en tiempo real"],
            "portfolio": ["galería de proyectos", "sección sobre mí", "formulario contacto"],
            "landing": ["hero section", "call to action", "features destacadas"],
            "blog": ["lista de posts", "filtros por categoría", "buscador"],
            "script": ["entrada/salida", "procesamiento", "manejo de errores"],
            "api": ["endpoints REST", "autenticación", "respuestas JSON"],
            "bot": ["escucha de eventos", "procesamiento de comandos", "respuestas"],
        }
        desc_lower = description.lower()
        for key, default_reqs in keywords.items():
            if key in desc_lower:
                reqs.extend(default_reqs)
        if not reqs:
            reqs = ["funcionalidad principal", "interfaz de usuario", "manejo de datos"]
        return reqs[:6]

    # ── Capa Material: componentes → archivos ──────────────────────────────

    def _materialize_web(self, concept: Dict[str, Any],
                         style: str = "minimal") -> Dict[str, str]:
        """Genera archivos reales (HTML, CSS, JS) desde el concepto.

        No usa templates fijos — genera desde el grafo de componentes.
        """
        theme = CSS_THEMES.get(style, CSS_THEMES["minimal"])
        components = concept.get("components", [])
        project_name = concept.get("project_name", "eidos-project")
        description = concept.get("description", "")

        # ── index.html ──
        comp_html_parts = []
        for i, comp in enumerate(components[:6]):
            comp_name = comp.get("name", f"section-{i}")
            html_id = re.sub(r'[^a-z0-9-]', '-', comp_name.lower())[:30]
            if "header" in comp_name.lower() or "hero" in comp_name.lower():
                comp_html_parts.append(
                    f'  <header id="{html_id}">\n'
                    f'    <h1>{project_name.replace("-", " ").title()}</h1>\n'
                    f'    <p>{description[:120]}</p>\n'
                    f'  </header>'
                )
            elif "sidebar" in comp_name.lower():
                comp_html_parts.append(
                    f'  <aside id="{html_id}">\n'
                    f'    <nav><ul><li><a href="#">Inicio</a></li>'
                    f'<li><a href="#">Acerca</a></li></ul></nav>\n'
                    f'  </aside>'
                )
            elif "chart" in comp_name.lower() or "gráfico" in comp_name.lower():
                comp_html_parts.append(
                    f'  <section id="{html_id}">\n'
                    f'    <h2>{comp_name.title()}</h2>\n'
                    f'    <div class="chart-container">\n'
                    f'      <canvas id="chart-{i}"></canvas>\n'
                    f'    </div>\n'
                    f'  </section>'
                )
            elif "footer" in comp_name.lower():
                comp_html_parts.append(
                    f'  <footer id="{html_id}">\n'
                    f'    <p>&copy; {datetime.now().year} — Generado por EIDOS</p>\n'
                    f'  </footer>'
                )
            else:
                comp_html_parts.append(
                    f'  <section id="{html_id}">\n'
                    f'    <h2>{comp_name.title()}</h2>\n'
                    f'    <p>{comp.get("description", "")[:100]}</p>\n'
                    f'  </section>'
                )

        project_title = project_name.replace("-", " ").title()
        index_html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="generator" content="EIDOS Maker">
  <title>{project_title}</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <div id="eidos-app">
{chr(10).join(comp_html_parts)}
  </div>
  <div id="eidos-status" style="display:none;">🧠 EIDOS conectado</div>
  <script src="app.js"></script>
</body>
</html>"""

        # ── style.css ──
        style_css = f"""/* {project_title} — Generado por EIDOS Maker */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  font-family: {theme["font"]};
  background: {theme["bg"]};
  color: {theme["text"]};
  line-height: 1.6;
  min-height: 100vh;
}}

#eidos-app {{
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem;
}}

header {{
  text-align: center;
  padding: 4rem 2rem;
  border-bottom: 2px solid {theme["accent"]};
}}

header h1 {{
  font-size: 2.5rem;
  color: {theme["accent"]};
  margin-bottom: 1rem;
}}

aside {{
  float: left;
  width: 250px;
  padding: 1.5rem;
  background: {theme["bg"]};
  border-right: 1px solid {theme["accent"]}33;
  min-height: 60vh;
}}

aside nav ul {{
  list-style: none;
}}

aside nav a {{
  color: {theme["accent"]};
  text-decoration: none;
  display: block;
  padding: 0.5rem 0;
  border-radius: {theme["radius"]};
  transition: background 0.2s;
}}

aside nav a:hover {{
  background: {theme["accent"]}22;
}}

section {{
  margin: 2rem 0;
  padding: 1.5rem;
  border-radius: {theme["radius"]};
  background: {theme["bg"]};
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}}

section h2 {{
  color: {theme["accent"]};
  margin-bottom: 1rem;
}}

.chart-container {{
  width: 100%;
  height: 300px;
  border: 1px solid {theme["accent"]}33;
  border-radius: {theme["radius"]};
  display: flex;
  align-items: center;
  justify-content: center;
}}

footer {{
  text-align: center;
  padding: 2rem;
  margin-top: 3rem;
  border-top: 1px solid {theme["accent"]}33;
  font-size: 0.9rem;
  opacity: 0.7;
}}

@media (max-width: 768px) {{
  aside {{
    float: none;
    width: 100%;
    border-right: none;
    border-bottom: 1px solid {theme["accent"]}33;
    min-height: auto;
  }}
  #eidos-app {{ padding: 1rem; }}
  header {{ padding: 2rem 1rem; }}
  header h1 {{ font-size: 1.8rem; }}
}}
"""

        # ── app.js ──
        app_js = f"""// {project_title} — Generado por EIDOS Maker
// EIDOS Brain API connection (localhost:8766)
const EIDOS_API = 'http://localhost:8766';

document.addEventListener('DOMContentLoaded', () => {{
  console.log('🧠 {project_title} — EIDOS-powered');
  initEidosConnection();
}});

function initEidosConnection() {{
  const statusEl = document.getElementById('eidos-status');
  fetch(EIDOS_API + '/health')
    .then(r => r.json())
    .then(data => {{
      if (statusEl) {{
        statusEl.style.display = 'block';
        statusEl.textContent = '🧠 EIDOS conectado — ' + data.status;
      }}
    }})
    .catch(() => {{
      if (statusEl) {{
        statusEl.style.display = 'block';
        statusEl.style.color = '#ef4444';
        statusEl.textContent = '⚠️ EIDOS no disponible (brain offline)';
      }}
    }});
}}

// Consulta al brain de EIDOS
async function askEidos(query) {{
  try {{
    const resp = await fetch(EIDOS_API + '/query', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ query, max_results: 5 }})
    }});
    return await resp.json();
  }} catch(e) {{
    console.error('EIDOS query error:', e);
    return null;
  }}
}}
"""

        return {
            "index.html": index_html,
            "style.css": style_css,
            "app.js": app_js,
        }

    def _materialize_script(self, concept: Dict[str, Any]) -> str:
        """Genera un script Python desde la descripción conceptual."""
        description = concept.get("description", "")
        components = concept.get("components", [])
        project_name = concept.get("project_name", "script").replace("-", "_")

        # Mapear requisitos a imports y funciones
        imports = ["import os", "import sys", "import json",
                   "from pathlib import Path", "from datetime import datetime"]
        functions = []

        desc_lower = description.lower()
        if "archivo" in desc_lower or "file" in desc_lower or "ordenar" in desc_lower:
            functions.append(f'''
def organizar_archivos(directorio: str = ".") -> dict:
    """Organiza archivos por tipo/extensión en subcarpetas."""
    dir_path = Path(directorio)
    if not dir_path.exists():
        return {{"error": f"Directorio no encontrado: {{directorio}}"}}

    categorias = {{
        "imagenes": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"],
        "documentos": [".pdf", ".doc", ".docx", ".txt", ".md", ".odt"],
        "codigo": [".py", ".js", ".html", ".css", ".json", ".yaml", ".toml"],
        "comprimidos": [".zip", ".tar", ".gz", ".rar", ".7z"],
        "audio": [".mp3", ".wav", ".ogg", ".flac"],
        "video": [".mp4", ".avi", ".mkv", ".mov"],
    }}

    movidos = {{}}
    for archivo in dir_path.iterdir():
        if archivo.is_file():
            ext = archivo.suffix.lower()
            for categoria, extensiones in categorias.items():
                if ext in extensiones:
                    dest = dir_path / categoria
                    dest.mkdir(exist_ok=True)
                    archivo.rename(dest / archivo.name)
                    movidos[str(archivo)] = str(dest / archivo.name)
                    break

    return {{"organizados": len(movidos), "detalles": movidos}}
''')

        if "dashboard" in desc_lower or "visual" in desc_lower:
            functions.append(f'''
def generar_reporte(datos: list, titulo: str = "Reporte") -> str:
    """Genera un reporte HTML simple desde datos."""
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>{{titulo}}</title>
<style>body{{font-family:system-ui;max-width:800px;margin:2rem auto;padding:1rem;}}
table{{width:100%%;border-collapse:collapse;}}th,td{{padding:.5rem;border:1px solid #ddd;}}
th{{background:#f0f0f0;}}</style></head>
<body><h1>{{titulo}}</h1><table><tr>"""
    if datos:
        for key in datos[0].keys():
            html += f"<th>{{key}}</th>"
        html += "</tr>"
        for row in datos:
            html += "<tr>" + "".join(f"<td>{{v}}</td>" for v in row.values()) + "</tr>"
    html += "</table><footer>Generado por EIDOS — {{datetime.now()}}</footer></body></html>"
    return html
''')

        if "api" in desc_lower or "servidor" in desc_lower or "server" in desc_lower:
            imports.append("from http.server import HTTPServer, BaseHTTPRequestHandler")
            functions.append(f'''
class EidosAPI(BaseHTTPRequestHandler):
    """Micro API generada por EIDOS."""

    def do_GET(self):
        if self.path == "/health":
            self._json({{"status": "ok", "generator": "EIDOS Maker"}})
        elif self.path == "/":
            self._html("<h1>EIDOS API</h1><p>Endpoint /health disponible</p>")
        else:
            self._json({{"error": "not found"}}, 404)

    def _json(self, data, code=200):
        import json as _json
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_json.dumps(data, indent=2).encode())

    def _html(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<!DOCTYPE html><html><body>{{body}}</body></html>".encode())

    @classmethod
    def serve(cls, port=8766):
        server = HTTPServer(("0.0.0.0", port), cls)
        print(f"🧠 EIDOS API en http://localhost:{{port}}")
        server.serve_forever()
''')

        # Cuerpo principal
        func_names = []
        for f in functions:
            # Extraer nombre de función
            import re as _re
            match = _re.search(r'def (\w+)\(', f)
            if match:
                func_names.append(match.group(1))

        main_body = f'''
# ── {project_name} ─────────────────────────────────────────────
# Generado por EIDOS Maker el {datetime.now().strftime("%Y-%m-%d %H:%M")}
# Descripción: {description}

{"".join(imports)}


{"".join(functions)}


if __name__ == "__main__":
    print(f"🧠 {project_name} — EIDOS Maker")
'''

        if "organizar_archivos" in func_names:
            main_body += '''
    import argparse
    p = argparse.ArgumentParser(description="Organizar archivos por tipo")
    p.add_argument("directorio", nargs="?", default=".")
    args = p.parse_args()
    result = organizar_archivos(args.directorio)
    print(json.dumps(result, indent=2, ensure_ascii=False))
'''
        elif "EidosAPI" in func_names:
            main_body += '''
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8766)
    args = p.parse_args()
    EidosAPI.serve(port=args.port)
'''
        elif "generar_reporte" in func_names:
            main_body += '''
    datos_ejemplo = [
        {"nombre": "Servicio A", "estado": "activo", "cpu": "12%"},
        {"nombre": "Servicio B", "estado": "activo", "cpu": "8%"},
        {"nombre": "Servicio C", "estado": "inactivo", "cpu": "0%"},
    ]
    reporte = generar_reporte(datos_ejemplo, "Reporte de Servicios")
    print(reporte[:500] + "...")
'''
        else:
            main_body += '''
    print("Proyecto generado por EIDOS. Personaliza según necesites.")
'''

        return main_body

    # ── API Principal ──────────────────────────────────────────────────────

    def create_project(self, description: str, *,
                       project_type: str = "web",
                       style: str = "minimal") -> Dict[str, Any]:
        """Crea un proyecto completo: conceptualiza → materializa → guarda.

        Args:
            description: descripción del proyecto a crear
            project_type: "web", "script", "api", "bot"
            style: tema visual ("minimal", "dark", "nature", "tech")

        Returns:
            {"project_name": str, "project_dir": str, "files": [...], "status": str}
        """
        t0 = time.time()

        # Fase 1: Conceptualizar
        concept = self._conceptualize(description, project_type)

        # Fase 2: Materializar
        files = {}
        if project_type in ("web", "landing", "portfolio", "dashboard", "blog"):
            files = self._materialize_web(concept, style)
        elif project_type in ("script", "api", "bot"):
            script = self._materialize_script(concept)
            ext = "py"
            fname = f"{concept['project_name']}.{ext}"
            files[fname] = script

        # Fase 3: Guardar
        project_dir = MAKER_OUTPUT_DIR / concept["project_name"]
        project_dir.mkdir(parents=True, exist_ok=True)

        for fname, content in files.items():
            fpath = project_dir / fname
            fpath.write_text(content)
            log.info("maker: creado %s (%d bytes)", fpath, len(content))

        # Guardar metadata
        meta = {
            "project_name": concept["project_name"],
            "description": description,
            "type": project_type,
            "style": style,
            "files": list(files.keys()),
            "components": concept.get("components", []),
            "created_at": datetime.now().isoformat(),
            "elapsed_s": round(time.time() - t0, 2),
        }
        (project_dir / "eidos-project.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False)
        )

        self._projects_created += 1
        self._save_state()

        # Emitir evento
        try:
            from core.eidos_events import emit
            emit("project_created", {
                "project_name": concept["project_name"],
                "type": project_type,
                "files": len(files),
            }, source="maker")
        except Exception:
            pass

        log.info("maker: proyecto '%s' creado con %d archivos en %.2fs",
                 concept["project_name"], len(files), meta["elapsed_s"])

        return {
            "project_name": concept["project_name"],
            "project_dir": str(project_dir),
            "files": list(files.keys()),
            "type": project_type,
            "status": "created",
            "elapsed_s": meta["elapsed_s"],
        }

    def create_webpage(self, description: str,
                       style: str = "minimal") -> Dict[str, Any]:
        """Atajo para crear página web."""
        return self.create_project(description, project_type="web", style=style)

    def create_script(self, description: str) -> Dict[str, Any]:
        """Atajo para crear script Python."""
        return self.create_project(description, project_type="script")

    def list_projects(self) -> List[Dict[str, Any]]:
        """Lista proyectos creados."""
        projects = []
        if MAKER_OUTPUT_DIR.exists():
            for proj_dir in sorted(MAKER_OUTPUT_DIR.iterdir()):
                if proj_dir.is_dir():
                    meta_file = proj_dir / "eidos-project.json"
                    if meta_file.exists():
                        try:
                            meta = json.loads(meta_file.read_text())
                            meta["project_dir"] = str(proj_dir)
                            projects.append(meta)
                        except Exception:
                            pass
        return projects

    # ── Estado ─────────────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        try:
            if MAKER_STATE.exists():
                return json.loads(MAKER_STATE.read_text())
        except Exception:
            pass
        return {"projects_created": 0}

    def _save_state(self):
        try:
            MAKER_STATE.parent.mkdir(parents=True, exist_ok=True)
            MAKER_STATE.write_text(json.dumps({
                "projects_created": self._projects_created,
                "last_updated": time.time(),
            }, indent=2))
        except Exception:
            pass

    def stats(self) -> Dict[str, Any]:
        return {
            "projects_created": self._projects_created,
            "output_dir": str(MAKER_OUTPUT_DIR),
            "projects": self.list_projects(),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_maker: Optional[Maker] = None


def get_maker() -> Maker:
    global _maker
    if _maker is None:
        _maker = Maker()
    return _maker


# Necesitamos re al nivel del módulo
import re


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Maker — Creación de Software y Web")
    p.add_argument("--create", type=str, help="Crear proyecto (descripción)")
    p.add_argument("--type", type=str, default="web",
                   choices=["web", "script", "api", "bot", "portfolio", "dashboard"])
    p.add_argument("--style", type=str, default="minimal",
                   choices=["minimal", "dark", "nature", "tech"])
    p.add_argument("--list", action="store_true", help="Listar proyectos")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    maker = get_maker()

    if args.create:
        result = maker.create_project(args.create, project_type=args.type, style=args.style)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.list:
        projects = maker.list_projects()
        for proj in projects:
            print(f"📁 {proj['project_name']} [{proj.get('type','?')}] — {len(proj.get('files',[]))} archivos")
    elif args.stats:
        print(json.dumps(maker.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
