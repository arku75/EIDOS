#!/usr/bin/env python3
"""
EIDOS Security Learning System
Aprende de cursos de hacking ético de manera responsable y legal
"""

import os
import sys
import json
import zipfile
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict
from datetime import datetime
import time

# Add EIDOS root to path
EIDOS_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.eidos_config import get_config
from core.learning_system import LearningSystem
from core.cleanup_system import CleanupManager
from core.objectives_system import get_objectives_manager, ObjectiveType, ObjectivePriority


@dataclass
class SecurityConcept:
    """Representa un concepto de seguridad aprendido"""
    concept_name: str
    category: str  # reconnaissance, exploitation, defense, analysis, etc.
    description: str
    ethical_context: str  # LEGAL/EDUCATIONAL/DEFENSIVE
    tools_involved: List[str]
    learned_from: str
    learned_at: float
    can_execute_safely: bool  # True solo si es en entorno controlado
    requires_permission: bool  # True si requiere autorización explícita


@dataclass
class SecurityCourse:
    """Representa un curso de hacking ético"""
    course_id: str
    title: str
    file_path: str
    file_size_mb: float
    format: str  # ZIP, PDF, VIDEO, TEXT
    topics_covered: List[str]
    tools_taught: List[str]
    status: str  # pending, learning, completed
    progress: float  # 0.0-1.0
    concepts_learned: int
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


class EthicalContext:
    """Define el contexto ético para todas las operaciones de seguridad"""

    ALLOWED_TARGETS = [
        "localhost",
        "127.0.0.1",
        "::1",
        "*.local",  # VMs locales
        "owned_domains",  # Dominios propiedad del usuario
    ]

    FORBIDDEN_ACTIONS = [
        "unauthorized_access",
        "data_theft",
        "service_disruption_public",
        "credential_harvesting_bulk",
        "malware_distribution",
    ]

    REQUIRED_PERMISSIONS = [
        "network_scanning",  # Requiere confirmación
        "exploitation_attempts",  # Requiere confirmación
        "credential_testing",  # Requiere confirmación
    ]

    @staticmethod
    def is_target_allowed(target: str) -> bool:
        """Verifica si un objetivo es permitido éticamente"""
        if not target:
            return False

        # Localhost siempre permitido
        if target in ["localhost", "127.0.0.1", "::1"]:
            return True

        # Dominios .local (VMs)
        if target.endswith(".local"):
            return True

        # Cualquier otro objetivo requiere verificación del usuario
        return False

    @staticmethod
    def requires_user_permission(action: str) -> bool:
        """Determina si una acción requiere permiso del usuario"""
        return action in EthicalContext.REQUIRED_PERMISSIONS

    @staticmethod
    def is_action_forbidden(action: str) -> bool:
        """Verifica si una acción está prohibida"""
        return action in EthicalContext.FORBIDDEN_ACTIONS


class SecurityLearningSystem:
    """Sistema especializado para aprender de cursos de hacking ético"""

    def __init__(self):
        self.config = get_config()
        self.learning = LearningSystem()
        self.cleanup = CleanupManager()
        self.objectives = get_objectives_manager()

        # Directorios
        self.courses_dir = Path("/home/ser/mis cosas/CURSOS/")
        self.security_kb = Path.home() / ".eidos" / "knowledge" / "security"
        self.security_kb.mkdir(parents=True, exist_ok=True)

        # Archivos de datos
        self.concepts_file = self.security_kb / "concepts.jsonl"
        self.courses_file = self.security_kb / "courses.jsonl"

        # Categorías de seguridad
        self.categories = {
            "reconnaissance": ["nmap", "rustscan", "masscan", "amass", "subfinder"],
            "exploitation": ["metasploit", "sqlmap", "burp", "evilginx", "beef"],
            "defense": ["firewall", "ids", "ips", "waf", "antivirus"],
            "analysis": ["wireshark", "tcpdump", "burp suite", "zaproxy"],
            "privilege_escalation": ["linpeas", "winpeas", "sudo", "suid"],
            "web_security": ["xss", "sqli", "csrf", "ssrf", "lfi", "rfi"],
            "network": ["wifi", "bluetooth", "mitm", "arp spoofing"],
            "social_engineering": ["phishing", "pretexting", "vishing"],
        }

    def scan_courses_directory(self) -> List[SecurityCourse]:
        """Escanea el directorio de cursos y identifica todos los cursos disponibles"""
        courses = []

        if not self.courses_dir.exists():
            print(f"⚠️  Directorio de cursos no encontrado: {self.courses_dir}")
            return courses

        print(f"🔍 Escaneando cursos en: {self.courses_dir}")

        # Escanear archivos ZIP
        for zip_file in self.courses_dir.glob("*.zip"):
            course = self._create_course_from_zip(zip_file)
            if course:
                courses.append(course)

        # Escanear subdirectorios
        for subdir in ["CURSOS EN TEXTO", "LIBROS", "ZIP FILES"]:
            subdir_path = self.courses_dir / subdir
            if subdir_path.exists():
                for file in subdir_path.iterdir():
                    if file.is_file() and not file.name.startswith('.'):
                        course = self._create_course_from_file(file)
                        if course:
                            courses.append(course)

        print(f"✅ Encontrados {len(courses)} cursos")
        return courses

    def _create_course_from_zip(self, zip_path: Path) -> Optional[SecurityCourse]:
        """Crea un objeto SecurityCourse desde un archivo ZIP"""
        try:
            size_mb = zip_path.stat().st_size / (1024 * 1024)

            # Intentar identificar tópicos del nombre del archivo
            topics = self._extract_topics_from_filename(zip_path.name)
            tools = self._extract_tools_from_filename(zip_path.name)

            course = SecurityCourse(
                course_id=f"zip_{zip_path.stem}",
                title=zip_path.stem,
                file_path=str(zip_path),
                file_size_mb=round(size_mb, 2),
                format="ZIP",
                topics_covered=topics,
                tools_taught=tools,
                status="pending",
                progress=0.0,
                concepts_learned=0
            )

            return course
        except Exception as e:
            print(f"⚠️  Error procesando {zip_path.name}: {e}")
            return None

    def _create_course_from_file(self, file_path: Path) -> Optional[SecurityCourse]:
        """Crea un objeto SecurityCourse desde cualquier archivo"""
        try:
            size_mb = file_path.stat().st_size / (1024 * 1024)

            # Determinar formato
            ext = file_path.suffix.lower()
            format_map = {
                ".pdf": "PDF",
                ".txt": "TEXT",
                ".md": "MARKDOWN",
                ".mp4": "VIDEO",
                ".avi": "VIDEO",
                ".mkv": "VIDEO",
            }
            file_format = format_map.get(ext, "UNKNOWN")

            topics = self._extract_topics_from_filename(file_path.name)
            tools = self._extract_tools_from_filename(file_path.name)

            course = SecurityCourse(
                course_id=f"{file_format.lower()}_{file_path.stem}",
                title=file_path.stem,
                file_path=str(file_path),
                file_size_mb=round(size_mb, 2),
                format=file_format,
                topics_covered=topics,
                tools_taught=tools,
                status="pending",
                progress=0.0,
                concepts_learned=0
            )

            return course
        except Exception as e:
            print(f"⚠️  Error procesando {file_path.name}: {e}")
            return None

    def _extract_topics_from_filename(self, filename: str) -> List[str]:
        """Extrae tópicos de seguridad del nombre del archivo"""
        filename_lower = filename.lower()
        topics = []

        for category, keywords in self.categories.items():
            for keyword in keywords:
                if keyword in filename_lower:
                    topics.append(category)
                    break

        # Tópicos generales
        general_keywords = {
            "pentest": "penetration_testing",
            "hacking": "ethical_hacking",
            "security": "cybersecurity",
            "exploit": "exploitation",
            "recon": "reconnaissance",
            "osint": "open_source_intelligence",
            "web": "web_security",
            "network": "network_security",
        }

        for keyword, topic in general_keywords.items():
            if keyword in filename_lower and topic not in topics:
                topics.append(topic)

        return topics

    def _extract_tools_from_filename(self, filename: str) -> List[str]:
        """Extrae herramientas de seguridad del nombre del archivo"""
        filename_lower = filename.lower()
        tools = []

        # Buscar en todas las categorías
        for keywords in self.categories.values():
            for tool in keywords:
                if tool in filename_lower and tool not in tools:
                    tools.append(tool)

        return tools

    def learn_from_course(self, course: SecurityCourse) -> Dict:
        """
        Aprende de un curso de hacking ético.

        🛡️ PROTECCIÓN: NUNCA borra archivos del usuario.
        - Archivos en /home/ser/mis cosas/CURSOS/ → PROTEGIDOS, no se borran
        - Archivos en ~/.eidos/downloads/ → Se pueden borrar después de aprender
        """
        print(f"\n📚 Aprendiendo de: {course.title}")
        print(f"   Formato: {course.format} ({course.file_size_mb} MB)")
        print(f"   Tópicos: {', '.join(course.topics_covered) if course.topics_covered else 'Por determinar'}")

        course.status = "learning"
        course.started_at = time.time()
        self._save_course(course)

        file_path = Path(course.file_path)
        concepts_learned = []

        try:
            if course.format == "ZIP":
                concepts = self._learn_from_zip_course(file_path)
                concepts_learned.extend(concepts)
            elif course.format == "PDF":
                concepts = self._learn_from_pdf_course(file_path)
                concepts_learned.extend(concepts)
            elif course.format in ["TEXT", "MARKDOWN"]:
                concepts = self._learn_from_text_course(file_path)
                concepts_learned.extend(concepts)
            elif course.format == "VIDEO":
                concepts = self._learn_from_video_course(file_path)
                concepts_learned.extend(concepts)

            # Actualizar curso
            course.status = "completed"
            course.progress = 1.0
            course.concepts_learned = len(concepts_learned)
            course.completed_at = time.time()
            self._save_course(course)

            # Guardar conceptos
            for concept in concepts_learned:
                self._save_concept(concept)

            # 🛡️ Auto-cleanup SOLO para archivos descargados por EIDOS
            # Los archivos del usuario en /home/ser/mis cosas/ NUNCA se borran
            if self.cleanup.auto_cleanup and course.format in ["ZIP", "PDF"]:
                knowledge_summary = f"Curso: {course.title}\nConceptos: {len(concepts_learned)}\nTópicos: {', '.join(course.topics_covered)}"
                # El cleanup manager verificará si es seguro borrar
                self.cleanup.mark_as_learned(file_path, knowledge_summary, can_delete=True)
                # Si el archivo estaba en /home/ser/mis cosas/, NO se borrará (protegido)
                # Si el archivo estaba en ~/.eidos/downloads/, SÍ se borrará

            print(f"✅ Curso completado: {len(concepts_learned)} conceptos aprendidos")

            return {
                "success": True,
                "course": course.title,
                "concepts_learned": len(concepts_learned),
                "topics": course.topics_covered,
                "tools": course.tools_taught,
            }

        except Exception as e:
            course.status = "pending"
            course.progress = 0.0
            self._save_course(course)
            print(f"❌ Error aprendiendo de curso: {e}")
            return {"success": False, "error": str(e)}

    def _learn_from_zip_course(self, zip_path: Path) -> List[SecurityConcept]:
        """Aprende de un curso en formato ZIP"""
        concepts = []

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_file:
                file_list = zip_file.namelist()

                # Analizar estructura
                structure = {
                    "scripts": [f for f in file_list if f.endswith(('.py', '.sh', '.rb', '.pl'))],
                    "docs": [f for f in file_list if f.endswith(('.txt', '.md', '.pdf'))],
                    "configs": [f for f in file_list if f.endswith(('.conf', '.cfg', '.ini', '.yaml', '.json'))],
                    "exploits": [f for f in file_list if 'exploit' in f.lower()],
                }

                # Extraer conceptos de la estructura
                if structure["exploits"]:
                    concept = SecurityConcept(
                        concept_name="Exploitation Techniques",
                        category="exploitation",
                        description=f"Curso contiene {len(structure['exploits'])} exploits",
                        ethical_context="EDUCATIONAL",
                        tools_involved=self._extract_tools_from_filename(zip_path.name),
                        learned_from=str(zip_path),
                        learned_at=time.time(),
                        can_execute_safely=False,  # Requiere entorno controlado
                        requires_permission=True
                    )
                    concepts.append(concept)

                if structure["scripts"]:
                    # Leer algunos scripts para entender herramientas
                    for script in structure["scripts"][:5]:  # Máximo 5 scripts
                        try:
                            content = zip_file.read(script).decode('utf-8', errors='ignore')
                            tools = self._identify_tools_in_code(content)

                            if tools:
                                concept = SecurityConcept(
                                    concept_name=f"Script: {Path(script).name}",
                                    category="reconnaissance" if "scan" in script.lower() else "exploitation",
                                    description=f"Script de seguridad usando: {', '.join(tools)}",
                                    ethical_context="EDUCATIONAL",
                                    tools_involved=tools,
                                    learned_from=f"{zip_path.name}/{script}",
                                    learned_at=time.time(),
                                    can_execute_safely=True,  # Scripts pueden ejecutarse en sandbox
                                    requires_permission=True
                                )
                                concepts.append(concept)
                        except Exception:
                            continue

                # Leer documentación si existe
                for doc in structure["docs"][:3]:  # Máximo 3 docs
                    try:
                        if doc.endswith('.txt') or doc.endswith('.md'):
                            content = zip_file.read(doc).decode('utf-8', errors='ignore')
                            doc_concepts = self._extract_concepts_from_text(content, str(zip_path))
                            concepts.extend(doc_concepts)
                    except Exception:
                        continue

        except Exception as e:
            print(f"⚠️  Error leyendo ZIP: {e}")

        return concepts

    def _learn_from_pdf_course(self, pdf_path: Path) -> List[SecurityConcept]:
        """Aprende de un curso en formato PDF"""
        concepts = []

        # Usar pdftotext si está disponible
        try:
            result = subprocess.run(
                ["pdftotext", str(pdf_path), "-"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                text = result.stdout
                concepts = self._extract_concepts_from_text(text, str(pdf_path))
        except Exception:
            print("⚠️  pdftotext no disponible, saltando PDF")

        return concepts

    def _learn_from_text_course(self, text_path: Path) -> List[SecurityConcept]:
        """Aprende de un curso en formato texto"""
        try:
            with open(text_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()

            return self._extract_concepts_from_text(text, str(text_path))
        except Exception as e:
            print(f"⚠️  Error leyendo texto: {e}")
            return []

    def _learn_from_video_course(self, video_path: Path) -> List[SecurityConcept]:
        """Aprende de un curso en formato video (metadata por ahora)"""
        # Por ahora solo extraer del nombre del archivo
        concept = SecurityConcept(
            concept_name=f"Video Course: {video_path.stem}",
            category="educational_content",
            description=f"Curso en video sobre {video_path.stem}",
            ethical_context="EDUCATIONAL",
            tools_involved=self._extract_tools_from_filename(video_path.name),
            learned_from=str(video_path),
            learned_at=time.time(),
            can_execute_safely=False,
            requires_permission=False
        )

        return [concept]

    def _extract_concepts_from_text(self, text: str, source: str) -> List[SecurityConcept]:
        """Extrae conceptos de seguridad de texto"""
        concepts = []
        text_lower = text.lower()

        # Buscar menciones de herramientas
        tools_found = set()
        for keywords in self.categories.values():
            for tool in keywords:
                if tool in text_lower:
                    tools_found.add(tool)

        if tools_found:
            concept = SecurityConcept(
                concept_name=f"Security Tools Overview",
                category="analysis",
                description=f"Documentación menciona: {', '.join(list(tools_found)[:10])}",
                ethical_context="EDUCATIONAL",
                tools_involved=list(tools_found),
                learned_from=source,
                learned_at=time.time(),
                can_execute_safely=False,
                requires_permission=True
            )
            concepts.append(concept)

        return concepts

    def _identify_tools_in_code(self, code: str) -> List[str]:
        """Identifica herramientas de seguridad en código"""
        code_lower = code.lower()
        tools = []

        # Buscar imports y comandos
        for keywords in self.categories.values():
            for tool in keywords:
                if tool in code_lower:
                    tools.append(tool)

        return list(set(tools))

    def _save_course(self, course: SecurityCourse):
        """Guarda información del curso"""
        with open(self.courses_file, 'a') as f:
            f.write(json.dumps(asdict(course)) + '\n')

    def _save_concept(self, concept: SecurityConcept):
        """Guarda un concepto de seguridad"""
        with open(self.concepts_file, 'a') as f:
            f.write(json.dumps(asdict(concept)) + '\n')

    def get_learned_concepts(self, category: Optional[str] = None) -> List[SecurityConcept]:
        """Obtiene conceptos aprendidos, opcionalmente filtrados por categoría"""
        concepts = []

        if not self.concepts_file.exists():
            return concepts

        with open(self.concepts_file, 'r') as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    concept = SecurityConcept(**data)

                    if category is None or concept.category == category:
                        concepts.append(concept)

        return concepts

    def create_learning_objectives_from_courses(self, courses: List[SecurityCourse]) -> int:
        """Crea objetivos de aprendizaje basados en los cursos encontrados"""
        created = 0

        for course in courses[:5]:  # Máximo 5 objetivos iniciales
            if course.status == "pending":
                title = f"Aprender: {course.title}"
                description = f"Estudiar curso de {course.format} sobre {', '.join(course.topics_covered) if course.topics_covered else 'seguridad'}"

                # Crear objetivo
                obj = self.objectives.create_objective(
                    title=title,
                    description=description,
                    objective_type=ObjectiveType.LEARNING,
                    priority=ObjectivePriority.HIGH,
                    reasoning=f"Curso de hacking ético de {course.file_size_mb:.1f}MB con tópicos: {', '.join(course.topics_covered)}"
                )

                if obj:
                    created += 1

        return created

    def get_statistics(self) -> Dict:
        """Obtiene estadísticas del sistema de aprendizaje de seguridad"""
        courses = []
        if self.courses_file.exists():
            with open(self.courses_file, 'r') as f:
                for line in f:
                    if line.strip():
                        courses.append(SecurityCourse(**json.loads(line)))

        concepts = self.get_learned_concepts()

        return {
            "total_courses": len(courses),
            "completed_courses": len([c for c in courses if c.status == "completed"]),
            "pending_courses": len([c for c in courses if c.status == "pending"]),
            "total_concepts": len(concepts),
            "categories": list(set([c.category for c in concepts])),
            "tools_learned": list(set([tool for c in concepts for tool in c.tools_involved])),
        }


def main():
    """Función principal para testing"""
    print("🔐 EIDOS Security Learning System")
    print("=" * 60)

    sec_learning = SecurityLearningSystem()

    # Escanear cursos
    courses = sec_learning.scan_courses_directory()

    if courses:
        print(f"\n📊 Resumen de cursos:")
        for course in courses:
            print(f"  • {course.title}")
            print(f"    Formato: {course.format}, Tamaño: {course.file_size_mb} MB")
            if course.topics_covered:
                print(f"    Tópicos: {', '.join(course.topics_covered)}")
            if course.tools_taught:
                print(f"    Herramientas: {', '.join(course.tools_taught)}")
            print()

        # Crear objetivos de aprendizaje
        created = sec_learning.create_learning_objectives_from_courses(courses)
        print(f"✅ Creados {created} objetivos de aprendizaje")

        # Estadísticas
        stats = sec_learning.get_statistics()
        print(f"\n📈 Estadísticas:")
        print(f"  Cursos totales: {stats['total_courses']}")
        print(f"  Cursos completados: {stats['completed_courses']}")
        print(f"  Conceptos aprendidos: {stats['total_concepts']}")
        if stats['tools_learned']:
            print(f"  Herramientas identificadas: {', '.join(stats['tools_learned'][:10])}")
    else:
        print("⚠️  No se encontraron cursos")


if __name__ == "__main__":
    main()
