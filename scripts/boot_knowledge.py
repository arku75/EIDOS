"""
Inyecta conocimiento fundamental en brain.db para que EIDOS
pueda responder preguntas comunes sobre Docker, Python, Linux, etc.
"""
import sqlite3, uuid, json, sys
from pathlib import Path

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"

KNOWLEDGE = [
    # Docker
    ("docker", "Docker es una plataforma de contenedores que permite empaquetar, distribuir y ejecutar aplicaciones en entornos aislados llamados contenedores. Usa imágenes como plantillas y contenedores como instancias ejecutables. Comandos clave: docker run, docker build, docker pull, docker compose.", "devops", 0.9, "boot"),
    ("docker compose", "Docker Compose es una herramienta para definir y ejecutar aplicaciones multi-contenedor con un solo archivo YAML (docker-compose.yml). Comando: docker compose up", "devops", 0.85, "boot"),
    ("docker run", "docker run [opciones] imagen [comando] — crea e inicia un contenedor desde una imagen. Opciones: -d (detach), -p (puertos), -v (volúmenes), --name (nombre)", "devops", 0.9, "boot"),
    ("docker image", "Una imagen Docker es una plantilla inmutable y ligera que contiene el sistema de archivos, dependencias y configuración para ejecutar un contenedor. Las imágenes se construyen con Dockerfile.", "devops", 0.9, "boot"),
    
    # Python
    ("python list", "Lista en Python: estructura mutable, ordenada, indexable. Sintaxis: [1, 2, 3]. Métodos: append, extend, insert, remove, pop, sort, reverse. Slicing: lista[inicio:fin:paso]. Comprensión: [x*2 for x in range(10)]", "coding", 0.9, "boot"),
    ("python dict", "Diccionario en Python: estructura clave-valor mutable. Sintaxis: {'key': 'value'}. Métodos: keys, values, items, get, update, pop. Los dict comprehension también existen.", "coding", 0.9, "boot"),
    ("python function", "Función en Python: bloque reutilizable definido con def. Sintaxis: def nombre(parametros): return valor. Args opcionales, *args, **kwargs. Lambdas: lambda x: x*2. Decoradores: @decorador", "coding", 0.9, "boot"),
    ("python module", "Módulo Python: archivo .py que exporta funciones, clases, variables. Import: import modulo / from modulo import cosa. Paquetes: directorio con __init__.py. PyPI: pip install nombre", "coding", 0.9, "boot"),
    
    # Linux
    ("linux bash", "Bash (Bourne Again Shell) es el intérprete de comandos principal en Linux. Scripts: #!/bin/bash. Variables: $VAR. Condicionales: if/then/elif/fi. Bucles: for/while/until. Funciones: nombre() { comandos; }", "sysadmin", 0.9, "boot"),
    ("chmod", "chmod cambia permisos de archivos Linux. Modo octal: chmod 755 archivo (rwxr-xr-x). Modo simbólico: chmod u+x archivo. Flags: -R recursivo. Permisos: r=4, w=2, x=1. Dueño/grupo/otros: u/g/o", "sysadmin", 0.9, "boot"),
    ("linux filesystem", "Sistema de archivos Linux: / (root), /home (usuarios), /etc (configuración), /var (datos variables), /tmp (temporal), /usr (programas), /bin (binarios esenciales), /dev (dispositivos), /proc (procesos virtual). FHS estándar.", "sysadmin", 0.9, "boot"),
    ("linux process", "Procesos Linux: ps (lista), top/htop (monitor), kill (terminar), nice/renice (prioridad). Estados: R (running), S (sleeping), D (uninterruptible), Z (zombie). /proc/{pid}/ contiene información del proceso.", "sysadmin", 0.9, "boot"),
    
    # Git
    ("git", "Git es un sistema de control de versiones distribuido. Flujo: git init → git add → git commit → git push. Ramas: git branch, git checkout, git merge. Remotos: git remote add origin URL. Historial: git log, git diff.", "devops", 0.9, "boot"),
    ("github", "GitHub es una plataforma de alojamiento de código usando Git. Características: repositorios remotos, pull requests, issues, Actions (CI/CD), Pages (hosting estático), Discussions, Projects.", "devops", 0.9, "boot"),
    
    # SQL / DB
    ("sqlite", "SQLite es una base de datos SQL embebida sin servidor. Un solo archivo .db. Tipos: TEXT, INTEGER, REAL, BLOB. Comandos: CREATE TABLE, SELECT, INSERT, UPDATE, DELETE. Índices, transacciones, FTS5 para búsqueda de texto.", "coding", 0.9, "boot"),
    ("chromadb", "ChromaDB es una base de datos vectorial para búsqueda semántica por embeddings. Almacena documentos + vectores. Búsqueda por similitud coseno. Ideal para RAG. Persistencia en disco con PersistentClient.", "ai", 0.9, "boot"),
    
    # EIDOS
    ("eidos architecture", "EIDOS es una colonia digital viva con arquitectura modular: core/ (180+ módulos Python), rust-core/ (módulos Rust para velocidad), web-panel/ (dashboard web). Componentes: colony_community (agentes), knowledge_reasoner (razonamiento), bridge_to_eidos (API en puerto 8003), ChromaDB (memoria semántica).", "ai", 0.95, "boot"),
    ("eidos bridge", "Bridge to EIDOS es un servidor Flask en puerto 8003 que expone 25+ endpoints REST. Funcionalidades: /talk (charlar con Colony), /reason (razonamiento semántico), /evolve (auto-evolución), /study (aprender temas), /inject (inyectar conocimiento proactivo), /improve (auto-mejora de código).", "ai", 0.95, "boot"),
    ("eidos reasoner", "Knowledge Reasoner es el motor de razonamiento semántico de EIDOS. Construye un grafo de 8000+ nodos de conocimiento con 650k relaciones. Usa FTS5 + tokens + BFS para responder preguntas combinando múltiples fuentes. Genera inferencias nuevas automáticamente.", "ai", 0.95, "boot"),
    ("ollama", "Ollama es un servidor local de modelos de lenguaje (LLMs). EIDOS usa: eidos:latest (modelo personalizado), qwen2.5:1.5b (rápido), hermes3:8b (potente), nomic-embed-text (embeddings para ChromaDB), moondream (visión). Puerto por defecto: 11434.", "ai", 0.9, "boot"),
    
    # Web
    ("nginx", "Nginx es un servidor web, proxy inverso y balanceador de carga. Configuración en /etc/nginx/. Comandos: nginx -t (test), systemctl reload nginx. Directivas: server, location, proxy_pass, upstream.", "devops", 0.85, "boot"),
    ("flask", "Flask es un micro-framework web Python. Rutas con @app.route(). GET/POST con methods=[]. request.args/forms/json para datos. render_template para HTML. Blueprints para modularidad. Puerto 5000 por defecto.", "coding", 0.9, "boot"),
    ("websocket", "WebSocket es un protocolo de comunicación bidireccional en tiempo real sobre TCP. Usa ws:// o wss:// (seguro). Ideal para dashboards en vivo, chats, monitoreo. EIDOS tiene monitor WebSocket en :8004, bridge en :8003.", "coding", 0.85, "boot"),
    
    # AI / ML
    ("rag", "RAG (Retrieval-Augmented Generation) es una técnica que combina búsqueda en base de conocimiento con generación de texto LLM. EIDOS la implementa con ChromaDB (búsqueda vectorial) + Ollama (generación). Mejora precisión y reduce alucinaciones.", "ai", 0.9, "boot"),
    ("embedding", "Embedding es una representación vectorial densa de texto en espacio continuo. nomic-embed-text genera embeddings de 768 dimensiones. Usados para búsqueda semántica en ChromaDB. Similitud coseno para comparar.", "ai", 0.85, "boot"),
    ("neural network", "Red neuronal: modelo computacional inspirado en el cerebro biológico. Capas: input, hidden, output. Pesos y biases aprendidos con backpropagation. Tipos: CNN (visión), RNN (texto), Transformer (LLMs). EIDOS usa embeddings y modelos locales via Ollama.", "ai", 0.85, "boot"),
]

def inject():
    conn = sqlite3.connect(str(BRAIN_DB), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    added = 0
    for concept, definition, category, confidence, source in KNOWLEDGE:
        try:
            cid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"boot:{concept}"))
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes "
                "(id, concept, definition, category, confidence, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cid, concept, definition, category, confidence, source)
            )
            if conn.total_changes > added:
                added = conn.total_changes
        except Exception as e:
            print(f"Error: {concept}: {e}")
    conn.commit()
    conn.close()
    print(f"Inyectados {added} nodos de conocimiento fundacional")
    
    # Also sync ChromaDB
    try:
        sys.path.insert(0, '/home/ser/EIDOS')
        from core.colony_chroma import get_chroma_memory
        c = get_chroma_memory()
        if c.is_ready():
            print(f"ChromaDB: {c.count()} vectores antes de sync")
    except Exception as e:
        print(f"Chroma sync: {e}")

if __name__ == "__main__":
    inject()
