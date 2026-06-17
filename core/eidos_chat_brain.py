#!/usr/bin/env python3
"""
EIDOS Chat Brain - Sistema de respuestas inteligentes
======================================================

Este módulo procesa preguntas del usuario y genera respuestas inteligentes
basándose en el conocimiento aprendido del Knowledge DB.

Características:
- Análisis de intención de la pregunta
- Respuestas contextuales basadas en código observado
- Sugerencias proactivas
- Análisis de patrones de código
"""

import re
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime


class EidosChatBrain:
    """
    Cerebro de chat de EIDOS - Procesa preguntas y genera respuestas inteligentes
    """

    def __init__(self, knowledge_db):
        self.knowledge_db = knowledge_db

        # Patrones de preguntas comunes
        self.question_patterns = {
            'lenguajes': r'(qué|cuáles|cuántos).*(lenguaje|language|idioma)',
            'bibliotecas': r'(qué|cuáles|cuántas).*(biblioteca|library|librería|lib|package)',
            'archivos': r'(qué|cuáles|cuántos).*(archivo|file)',
            'ayuda': r'(ayuda|help|cómo|como|puedo)',
            'capacidades': r'(qué puedes|what can|capacidad|función)',
            'mejor': r'(mejor|best|optim|recomiend|suggest)',
            'aprender': r'(aprend|learn|entrenar|train)',
            'proyecto': r'(proyecto|project|código|code)',
            'rust': r'rust',
            'python': r'python|py',
            'javascript': r'javascript|js|node',
            'frameworks': r'framework',
            'estadísticas': r'(estadística|stat|métrica|metric)',
        }

    def answer(self, question: str) -> str:
        """
        Procesa una pregunta y devuelve una respuesta inteligente
        """
        question_lower = question.lower().strip()

        # Detectar intención de la pregunta
        intent = self._detect_intent(question_lower)

        # Generar respuesta basada en la intención
        if intent == 'greeting':
            return self._greeting_response()
        elif intent == 'lenguajes':
            return self._languages_response()
        elif intent == 'bibliotecas':
            return self._libraries_response(question_lower)
        elif intent == 'archivos':
            return self._files_response()
        elif intent == 'ayuda':
            return self._help_response()
        elif intent == 'capacidades':
            return self._capabilities_response()
        elif intent == 'mejor':
            return self._recommendations_response()
        elif intent == 'aprender':
            return self._learning_response()
        elif intent == 'proyecto':
            return self._project_analysis_response()
        elif intent == 'estadísticas':
            return self._statistics_response()
        elif intent == 'specific_language':
            # Pregunta sobre un lenguaje específico
            return self._specific_language_response(question_lower)
        else:
            return self._general_response(question_lower)

    def _detect_intent(self, question: str) -> str:
        """Detecta la intención de la pregunta"""

        # Saludos
        if re.search(r'^(hola|hi|hello|hey|buenos|buenas)', question):
            return 'greeting'

        # Verificar cada patrón
        for intent, pattern in self.question_patterns.items():
            if re.search(pattern, question, re.IGNORECASE):
                if intent in ['rust', 'python', 'javascript']:
                    return 'specific_language'
                return intent

        return 'general'

    def _greeting_response(self) -> str:
        """Respuesta a saludos"""
        langs = len(self.knowledge_db.languages)
        libs = len(self.knowledge_db.libraries)

        responses = [
            f"¡Hola! Soy EIDOS, tu asistente de IA para desarrollo.",
            f"He estado observando tu código y ya conozco {langs} lenguajes y {libs} bibliotecas.",
            "",
            "Puedes preguntarme:",
            "• ¿Qué lenguajes he usado?",
            "• ¿Qué bibliotecas conoces?",
            "• ¿Qué recomiendas para mi proyecto?",
            "• Analiza mi código Python/Rust/JavaScript",
            "• Dame estadísticas de mi código",
        ]

        return "\n".join(responses)

    def _languages_response(self) -> str:
        """Respuesta sobre lenguajes"""
        if not self.knowledge_db.languages:
            return "Aún no he observado ningún archivo de código. Abre algunos archivos en VSEIDOS para que pueda aprender."

        response_lines = [
            f"📊 He observado {len(self.knowledge_db.languages)} lenguaje(s) de programación:\n"
        ]

        # Ordenar por archivos observados
        sorted_langs = sorted(
            self.knowledge_db.languages.items(),
            key=lambda x: getattr(x[1], 'files_observed', 0),
            reverse=True
        )

        for lang_name, lang_data in sorted_langs:
            files = getattr(lang_data, 'files_observed', 0)
            libs = list(getattr(lang_data, 'libraries', set()))

            response_lines.append(f"🔹 **{lang_name.upper()}**")
            response_lines.append(f"   • Archivos observados: {files}")

            if libs:
                response_lines.append(f"   • Bibliotecas detectadas: {len(libs)}")
                # Mostrar las 5 más comunes
                top_libs = libs[:5]
                response_lines.append(f"   • Principales: {', '.join(top_libs)}")

            response_lines.append("")

        return "\n".join(response_lines)

    def _libraries_response(self, question: str) -> str:
        """Respuesta sobre bibliotecas"""
        if not self.knowledge_db.libraries:
            return "Aún no he detectado bibliotecas. Programa más para que pueda aprender qué bibliotecas usas."

        # Filtrar por lenguaje si se menciona
        language_filter = None
        if 'python' in question:
            language_filter = 'python'
        elif 'rust' in question:
            language_filter = 'rust'
        elif 'javascript' in question or 'js' in question:
            language_filter = 'javascript'

        response_lines = []

        if language_filter:
            response_lines.append(f"📚 Bibliotecas de {language_filter.upper()} que conozco:\n")
            libs = [(name, data) for name, data in self.knowledge_db.libraries.items()
                    if getattr(data, 'language', '') == language_filter]
        else:
            response_lines.append(f"📚 He detectado {len(self.knowledge_db.libraries)} bibliotecas en total:\n")
            libs = list(self.knowledge_db.libraries.items())

        if not libs:
            return f"No he visto bibliotecas de {language_filter} aún."

        # Agrupar por lenguaje
        by_language = {}
        for lib_name, lib_data in libs:
            lang = getattr(lib_data, 'language', 'Unknown')
            if lang not in by_language:
                by_language[lang] = []
            by_language[lang].append(lib_name)

        for lang, lib_list in sorted(by_language.items()):
            response_lines.append(f"**{lang.upper()}:** {', '.join(sorted(lib_list)[:10])}")
            if len(lib_list) > 10:
                response_lines.append(f"   ... y {len(lib_list) - 10} más")
            response_lines.append("")

        return "\n".join(response_lines)

    def _files_response(self) -> str:
        """Respuesta sobre archivos observados"""
        total_files = sum(
            getattr(lang_data, 'files_observed', 0)
            for lang_data in self.knowledge_db.languages.values()
        )

        if total_files == 0:
            return "Aún no he observado archivos. Abre archivos de código en VSEIDOS para que empiece a aprender."

        response_lines = [
            f"📁 He observado {total_files} archivo(s) en total:\n"
        ]

        for lang_name, lang_data in self.knowledge_db.languages.items():
            files = getattr(lang_data, 'files_observed', 0)
            if files > 0:
                response_lines.append(f"• {lang_name}: {files} archivo(s)")

        return "\n".join(response_lines)

    def _help_response(self) -> str:
        """Respuesta de ayuda"""
        return """🤖 **Comandos y preguntas que entiendo:**

**Información básica:**
• ¿Qué lenguajes conozco?
• ¿Qué bibliotecas has detectado?
• ¿Cuántos archivos has observado?
• Dame estadísticas

**Análisis específico:**
• Analiza mi código Python/Rust/JavaScript
• ¿Qué frameworks uso?
• ¿Qué bibliotecas de Python conozco?

**Recomendaciones:**
• ¿Qué me recomiendas?
• ¿Cómo puedo mejorar mi código?
• ¿Qué debería aprender?

**Entrenamiento:**
• ¿Cómo puedo ayudarte a aprender más?
• ¿Qué necesitas para mejorar?

💡 **Tip:** Entre más programes en VSEIDOS, más inteligentes serán mis respuestas."""

    def _capabilities_response(self) -> str:
        """Respuesta sobre capacidades"""
        return """🧠 **Mis capacidades actuales:**

✅ **Observación en tiempo real:**
   • Detecto lenguajes automáticamente
   • Extraigo bibliotecas de tu código
   • Analizo patrones de programación

✅ **Conocimiento acumulativo:**
   • Aprendo de cada archivo que abres
   • Recuerdo todas las bibliotecas que usas
   • Construyo un perfil de tu stack tecnológico

✅ **Análisis de código:**
   • Identifico frameworks comunes
   • Detecto dependencias
   • Analizo complejidad (en desarrollo)

🚧 **En desarrollo (Fase 2):**
   • Aprendizaje offline 24/7
   • Sincronización P2P con otros EIDOS
   • Code review automático
   • Sugerencias de optimización
   • Detección de vulnerabilidades

💡 Actualmente he observado:
   • {langs} lenguaje(s)
   • {libs} biblioteca(s)
   • {files} archivo(s)
""".format(
            langs=len(self.knowledge_db.languages),
            libs=len(self.knowledge_db.libraries),
            files=sum(getattr(ld, 'files_observed', 0) for ld in self.knowledge_db.languages.values())
        )

    def _recommendations_response(self) -> str:
        """Respuesta con recomendaciones"""
        if not self.knowledge_db.languages:
            return "Necesito observar tu código primero para darte recomendaciones personalizadas."

        response_lines = ["💡 **Recomendaciones basadas en tu código:**\n"]

        # Analizar stack actual
        has_python = 'python' in self.knowledge_db.languages
        has_rust = 'rust' in self.knowledge_db.languages
        has_js = 'javascript' in self.knowledge_db.languages

        # Recomendaciones según stack
        if has_python:
            python_libs = [
                name for name, data in self.knowledge_db.libraries.items()
                if getattr(data, 'language', '') == 'python'
            ]

            if 'flask' in python_libs and 'fastapi' not in python_libs:
                response_lines.append("🔹 Usas Flask. Considera **FastAPI** para APIs más rápidas y con tipos automáticos.")

            if 'numpy' in python_libs or 'pandas' in python_libs:
                response_lines.append("🔹 Trabajas con datos. Considera **Polars** (más rápido que Pandas).")

        if has_rust and has_python:
            response_lines.append("🔹 Usas Rust y Python. Considera **PyO3** para crear extensiones Python en Rust (100x más rápidas).")

        if has_js:
            js_libs = [
                name for name, data in self.knowledge_db.libraries.items()
                if getattr(data, 'language', '') == 'javascript'
            ]

            if 'express' in js_libs:
                response_lines.append("🔹 Usas Express. Considera **Fastify** para mejor rendimiento.")

        # Recomendaciones generales
        response_lines.append("\n**Para aprender más rápido:**")
        response_lines.append("• Abre más proyectos en VSEIDOS")
        response_lines.append("• Edita archivos existentes")
        response_lines.append("• Cuanto más programes, mejores serán mis sugerencias")

        return "\n".join(response_lines)

    def _learning_response(self) -> str:
        """Respuesta sobre aprendizaje"""
        return """🎓 **Cómo ayudarme a aprender más:**

**1. Usa VSEIDOS para todos tus proyectos**
   • Abre archivos: `vseidos ~/tu-proyecto`
   • Edita código normalmente
   • Yo observo y aprendo automáticamente

**2. Trabaja en proyectos diversos**
   • Diferentes lenguajes → Aprendo más
   • Diferentes frameworks → Mejor contexto
   • Diferentes patrones → Más inteligente

**3. Entrenamiento masivo (opcional)**
   • Puedo analizar todos tus proyectos existentes
   • Pregunta: "¿Cómo entreno EIDOS con mis proyectos?"

**4. Futuro: P2P Sync**
   • Tu amigo programa Django → Su EIDOS aprende
   • Se sincroniza contigo → TU EIDOS conoce Django
   • Red global de conocimiento compartido

📊 **Mi progreso actual:**
   • Lenguajes conocidos: {langs}
   • Bibliotecas detectadas: {libs}
   • Archivos procesados: {files}

💡 **Objetivo:** Convertirme en tu asistente personalizado que conoce TODO tu stack tecnológico.
""".format(
            langs=len(self.knowledge_db.languages),
            libs=len(self.knowledge_db.libraries),
            files=sum(getattr(ld, 'files_observed', 0) for ld in self.knowledge_db.languages.values())
        )

    def _project_analysis_response(self) -> str:
        """Análisis del proyecto"""
        if not self.knowledge_db.languages:
            return "Aún no he visto suficiente código para analizar tu proyecto."

        response_lines = ["🔍 **Análisis de tu proyecto:**\n"]

        # Stack tecnológico
        response_lines.append("**Stack Tecnológico:**")
        for lang in self.knowledge_db.languages.keys():
            response_lines.append(f"  • {lang.upper()}")

        # Tipo de proyecto (inferir)
        libs = set(self.knowledge_db.libraries.keys())

        response_lines.append("\n**Tipo de Proyecto (inferido):**")

        if 'flask' in libs or 'fastapi' in libs or 'express' in libs:
            response_lines.append("  🌐 Aplicación Web / API")

        if 'tokio' in libs or 'actix' in libs:
            response_lines.append("  ⚡ Aplicación asíncrona de alto rendimiento")

        if 'numpy' in libs or 'pandas' in libs or 'sklearn' in libs:
            response_lines.append("  📊 Ciencia de datos / Machine Learning")

        if 'mongoose' in libs or 'sqlalchemy' in libs:
            response_lines.append("  🗄️ Usa bases de datos")

        if 'react' in libs or 'vue' in libs:
            response_lines.append("  🎨 Frontend moderno")

        # Complejidad
        total_files = sum(getattr(ld, 'files_observed', 0) for ld in self.knowledge_db.languages.values())

        response_lines.append(f"\n**Escala:**")
        if total_files < 10:
            response_lines.append("  📦 Proyecto pequeño/prototipo")
        elif total_files < 50:
            response_lines.append("  📚 Proyecto mediano")
        else:
            response_lines.append("  🏢 Proyecto grande/productivo")

        return "\n".join(response_lines)

    def _statistics_response(self) -> str:
        """Respuesta con estadísticas"""
        total_files = sum(
            getattr(ld, 'files_observed', 0)
            for ld in self.knowledge_db.languages.values()
        )

        return f"""📊 **Estadísticas de EIDOS:**

**Conocimiento acumulado:**
  • Lenguajes: {len(self.knowledge_db.languages)}
  • Bibliotecas: {len(self.knowledge_db.libraries)}
  • Archivos observados: {total_files}

**Distribución por lenguaje:**
{self._language_distribution()}

**Frameworks detectados:**
{self._detect_frameworks()}

**Última actualización:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    def _language_distribution(self) -> str:
        """Distribución de archivos por lenguaje"""
        if not self.knowledge_db.languages:
            return "  (Sin datos aún)"

        lines = []
        for lang, data in self.knowledge_db.languages.items():
            files = getattr(data, 'files_observed', 0)
            lines.append(f"  • {lang}: {files} archivo(s)")

        return "\n".join(lines) if lines else "  (Sin datos)"

    def _detect_frameworks(self) -> str:
        """Detecta frameworks conocidos"""
        frameworks = {
            'flask': 'Flask (Python Web)',
            'fastapi': 'FastAPI (Python Web)',
            'django': 'Django (Python Web)',
            'express': 'Express (Node.js Web)',
            'react': 'React (Frontend)',
            'vue': 'Vue.js (Frontend)',
            'tokio': 'Tokio (Rust async)',
            'actix': 'Actix (Rust Web)',
            'axios': 'Axios (HTTP client)',
            'pandas': 'Pandas (Data Science)',
            'numpy': 'NumPy (Data Science)',
            'sklearn': 'Scikit-learn (ML)',
        }

        detected = []
        for lib_name in self.knowledge_db.libraries.keys():
            if lib_name in frameworks:
                detected.append(f"  • {frameworks[lib_name]}")

        return "\n".join(detected) if detected else "  (Ninguno detectado aún)"

    def _specific_language_response(self, question: str) -> str:
        """Respuesta sobre un lenguaje específico"""
        # Detectar qué lenguaje pregunta
        lang = None
        if 'python' in question or 'py' in question:
            lang = 'python'
        elif 'rust' in question:
            lang = 'rust'
        elif 'javascript' in question or 'js' in question:
            lang = 'javascript'

        if not lang or lang not in self.knowledge_db.languages:
            return f"Aún no he observado código de {lang or 'ese lenguaje'}. Programa algo en {lang or 'él'} para que pueda aprender."

        lang_data = self.knowledge_db.languages[lang]
        files = getattr(lang_data, 'files_observed', 0)
        libs = list(getattr(lang_data, 'libraries', set()))

        response_lines = [
            f"🔍 **Análisis de tu código {lang.upper()}:**\n",
            f"📁 Archivos observados: {files}",
            f"📚 Bibliotecas detectadas: {len(libs)}\n"
        ]

        if libs:
            response_lines.append("**Bibliotecas principales:**")
            for lib in libs[:10]:
                response_lines.append(f"  • {lib}")
            if len(libs) > 10:
                response_lines.append(f"  ... y {len(libs) - 10} más")

        return "\n".join(response_lines)

    def _general_response(self, question: str) -> str:
        """Respuesta general"""
        langs = len(self.knowledge_db.languages)
        libs = len(self.knowledge_db.libraries)

        return f"""He observado {langs} lenguaje(s) y {libs} biblioteca(s).

💡 Puedes preguntarme:
  • "¿Qué lenguajes conoces?"
  • "¿Qué bibliotecas has detectado?"
  • "Analiza mi código Python"
  • "Dame estadísticas"
  • "¿Qué me recomiendas?"
  • "Ayuda"

🎓 Entre más programes en VSEIDOS, más inteligente me vuelvo.
"""


# Singleton global
_chat_brain_instance = None


def get_chat_brain(knowledge_db):
    """Obtiene instancia singleton del chat brain"""
    global _chat_brain_instance
    if _chat_brain_instance is None:
        _chat_brain_instance = EidosChatBrain(knowledge_db)
    return _chat_brain_instance
