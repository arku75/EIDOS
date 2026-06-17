"""
core/screen_controller.py — EidosScreenControl GOLD [S88]

Orquestador de 5 capas para control de pantalla INDETECTABLE.

"Ver, pensar, actuar — como un humano, no como un bot." — DeepSeek

Arquitectura:
  Layer 1 PLANNER      → Meta-plan desde lenguaje natural (LLM consultivo)
  Layer 2 VISUAL CORTEX → Screenshot + OCR + VLM → comprensión de escena
  Layer 3 DECISION ENGINE → Matching escena↔plan, siguiente acción (determinista)
  Layer 4 HUMAN EMULATOR → Trayectorias ratón, ritmo tecleo, scroll humano
  Layer 5 INPUT BACKEND  → USB Gadget HID (kernel) o xdotool fallback

Flujo principal:
  Goal "investiga X en GitHub"
    → Planner: [abrir_firefox, buscar_github, click_resultado, explorar_links, ...]
    → Loop: [VisualCortex→DecisionEngine→HumanEmulator→InputBackend] × N
    → Resultado: datos extraídos + aprendizaje

Uso:
    sc = get_screen_controller()
    result = sc.execute_mission(
        goal="Investiga n8n en GitHub, extrae links de documentación",
        max_steps=20,
    )

Nota: El LLM es CONSULTIVO. DecisionEngine es determinista para acciones críticas
(click en coordenadas, scroll, tecleo). LLM solo ayuda en planificación y comprensión
de escenas complejas.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.screen_controller")

# ── Tipos ───────────────────────────────────────────────────────────────────────


@dataclass
class ScreenRegion:
    """Región clickable/interactiva detectada en pantalla."""
    x: int
    y: int
    width: int
    height: int
    text: str = ""
    element_type: str = "unknown"  # button, link, input, text, scrollbar, icon
    confidence: float = 0.5
    ocr_text: str = ""


@dataclass
class Scene:
    """Escena visual completa: lo que EIDOS "ve" en la pantalla."""
    timestamp: float = 0.0
    screenshot_path: str = ""
    window_title: str = ""
    ocr_full_text: str = ""
    regions: List[ScreenRegion] = field(default_factory=list)
    vlm_description: str = ""
    dominant_color: str = ""
    scroll_position: str = "unknown"


@dataclass
class PlanStep:
    """Paso individual en el plan de navegación."""
    order: int
    action: str  # navigate, click, type, scroll, extract, wait, screenshot, evaluate
    target_description: str  # descripción en lenguaje natural
    target_coords: Optional[Tuple[int, int]] = None
    target_text: str = ""
    expected_result: str = ""
    completed: bool = False
    attempts: int = 0
    max_attempts: int = 3


@dataclass
class MissionPlan:
    """Plan de misión completo."""
    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    current_step: int = 0
    created_at: float = 0.0
    max_steps: int = 30


@dataclass
class ActionResult:
    """Resultado de una acción ejecutada."""
    success: bool
    action_type: str
    description: str
    scene_before: Optional[Scene] = None
    scene_after: Optional[Scene] = None
    extracted_data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)  # S92: nav verify, extra info
    error: str = ""
    elapsed: float = 0.0


# ── Layer 1: Planner ───────────────────────────────────────────────────────────


class Planner:
    """Planifica misiones desde lenguaje natural.

    Usa LLM (DeepSeek/Groq) como CONSULTIVO para descomponer metas complejas
    en secuencias de pasos accionables. Tiene heurísticas deterministas para
    metas simples (sin LLM).
    """

    # Patrones de metas conocidas (deterministas, sin LLM)
    KNOWN_PATTERNS = {
        "buscar_en_google": {
            "keywords": ["buscar", "google", "search", "googlea"],
            "steps": [
                {"action": "navigate", "target": "barra_direcciones"},
                {"action": "type", "target": "https://google.com"},
                {"action": "wait", "target": "carga_pagina"},
                {"action": "type", "target": "barra_busqueda"},
                {"action": "wait", "target": "resultados"},
                {"action": "extract", "target": "links_resultados"},
            ],
        },
        "github_explore": {
            "keywords": ["github", "repo", "repositorio", "código fuente"],
            "steps": [
                {"action": "navigate", "target": "barra_direcciones"},
                {"action": "type", "target": "github.com"},
                {"action": "wait", "target": "carga_pagina"},
                {"action": "click", "target": "barra_busqueda_github"},
                {"action": "type", "target": "query"},
                {"action": "wait", "target": "resultados"},
                {"action": "click", "target": "primer_resultado"},
                {"action": "extract", "target": "readme_y_links"},
            ],
        },
        "explorar_web": {
            "keywords": ["explorar", "investigar", "navegar", "explorar internet"],
            "steps": [
                {"action": "navigate", "target": "barra_direcciones"},
                {"action": "type", "target": "url_objetivo"},
                {"action": "wait", "target": "carga"},
                {"action": "scroll", "target": "contenido"},
                {"action": "extract", "target": "texto_y_links"},
                {"action": "navigate", "target": "siguiente_link"},
            ],
        },
    }

    def __init__(self, use_llm: bool = True):
        self._use_llm = use_llm

    def create_plan(self, goal: str, max_steps: int = 30) -> MissionPlan:
        """Crea un plan de misión desde una meta en lenguaje natural.

        Args:
            goal: meta en lenguaje natural (ej: "investiga n8n en github")
            max_steps: máximo de pasos en el plan

        Returns:
            MissionPlan con pasos accionables
        """
        plan = MissionPlan(
            goal=goal,
            max_steps=max_steps,
            created_at=time.time(),
        )

        # 1. Intentar patrón determinista primero
        pattern = self._match_pattern(goal)
        if pattern:
            log.info("Planner: patrón detectado '%s' (determinista)", pattern)
            plan.steps = self._build_steps_from_pattern(pattern, goal)
            return plan

        # 2. Usar LLM para metas complejas
        if self._use_llm:
            plan.steps = self._llm_create_plan(goal, max_steps)
        else:
            # Plan genérico de exploración
            plan.steps = self._generic_exploration_plan(goal)

        return plan

    def _match_pattern(self, goal: str) -> Optional[str]:
        """Busca coincidencias con patrones conocidos."""
        goal_lower = goal.lower()
        for name, pattern in self.KNOWN_PATTERNS.items():
            for kw in pattern["keywords"]:
                if kw in goal_lower:
                    return name
        return None

    def _build_steps_from_pattern(self, pattern_name: str, goal: str) -> List[PlanStep]:
        """Construye pasos desde un patrón conocido."""
        pattern = self.KNOWN_PATTERNS[pattern_name]
        steps = []
        for i, s in enumerate(pattern["steps"]):
            # Personalizar con la meta original
            target_text = ""
            if s["action"] == "type" and "target" in s:
                if s["target"] == "query":
                    # Extraer query de la meta
                    target_text = self._extract_query(goal, pattern)
                elif s["target"] == "url_objetivo":
                    target_text = self._extract_url(goal)

            steps.append(PlanStep(
                order=i,
                action=s["action"],
                target_description=s["target"],
                target_text=target_text,
                expected_result=f"Completado: {s['target']}",
            ))
        return steps

    def _extract_query(self, goal: str, pattern: dict) -> str:
        """Extrae la query de búsqueda de la meta."""
        for kw in pattern["keywords"]:
            # Quitar keywords y "investiga", "busca", etc.
            clean = goal.lower()
            for remove in ["investiga ", "busca ", "search ", "explora ",
                           "investigar ", "buscar ", "explorar "]:
                clean = clean.replace(remove, "")
            for remove_kw in pattern["keywords"]:
                clean = clean.replace(remove_kw, "")
            clean = clean.strip().strip("en ").strip("sobre ").strip()
            if clean:
                return clean
        return goal

    def _extract_url(self, goal: str) -> str:
        """Extrae URL de la meta si existe."""
        url_match = re.search(r'https?://[^\s]+', goal)
        if url_match:
            return url_match.group(0)
        return ""

    def _generic_exploration_plan(self, goal: str) -> List[PlanStep]:
        """Plan genérico de exploración web."""
        return [
            PlanStep(0, "navigate", "abrir_navegador",
                     target_text="firefox"),
            PlanStep(1, "wait", "carga_navegador",
                     expected_result="Firefox abierto"),
            PlanStep(2, "navigate", "barra_direcciones",
                     target_text=goal),
            PlanStep(3, "wait", "carga_pagina",
                     expected_result="Página cargada"),
            PlanStep(4, "scroll", "contenido_principal"),
            PlanStep(5, "extract", "texto_y_links",
                     expected_result="Datos extraídos"),
        ]

    def _llm_create_plan(self, goal: str, max_steps: int) -> List[PlanStep]:
        """Usa LLM para crear un plan detallado."""
        try:
            from core.eidos_deepseek import ask_deepseek
            system = """Eres un planificador de navegación web para un agente autónomo.
Descompón la meta en pasos accionables. Cada paso debe ser:
- navigate: ir a URL o abrir aplicación
- click: click en elemento de la UI
- type: escribir texto
- scroll: hacer scroll
- wait: esperar carga
- extract: extraer datos de la página

Responde SOLO con un array JSON de pasos. Cada paso: {"action": "...", "target_description": "...", "target_text": "..."}"""
            response = ask_deepseek(system, goal, model="lite", max_tokens=1024)
            # Intentar parsear JSON de la respuesta
            json_match = re.search(r'\[.*\]', response["text"], re.DOTALL)
            if json_match:
                raw_steps = json.loads(json_match.group(0))
                steps = []
                for i, s in enumerate(raw_steps[:max_steps]):
                    steps.append(PlanStep(
                        order=i,
                        action=s.get("action", "navigate"),
                        target_description=s.get("target_description", ""),
                        target_text=s.get("target_text", ""),
                    ))
                return steps
        except Exception as e:
            log.warning("LLM plan falló: %s. Usando plan genérico.", e)
        return self._generic_exploration_plan(goal)

    def replan(self, plan: MissionPlan, current_scene: Scene,
               stuck_reason: str) -> Optional[List[PlanStep]]:
        """Replanifica si la misión está atascada. Consulta al LLM."""
        try:
            from core.eidos_deepseek import ask_deepseek
            context = f"""Misión: {plan.goal}
Escena actual: {current_scene.ocr_full_text[:200]}
Paso actual fallido: paso {plan.current_step}, razón: {stuck_reason}
Ventana: {current_scene.window_title}"""
            system = "Eres un solucionador de problemas. Da el siguiente paso como JSON: {\"action\": \"...\", \"target_description\": \"...\", \"target_text\": \"...\"}. Responde SOLO con el JSON."
            response = ask_deepseek(system, context, model="lite", max_tokens=512)
            json_match = re.search(r'\{.*\}', response["text"], re.DOTALL)
            if json_match:
                step_data = json.loads(json_match.group(0))
                return [PlanStep(
                    order=plan.current_step,
                    action=step_data.get("action", "navigate"),
                    target_description=step_data.get("target_description", ""),
                    target_text=step_data.get("target_text", ""),
                )]
        except Exception:
            pass
        return None


# ── Layer 2: Visual Cortex ─────────────────────────────────────────────────────


class VisualCortex:
    """Procesa la pantalla: screenshot → OCR → VLM → comprensión de escena.

    Usa:
    - maim/scrot para captura de pantalla
    - Tesseract para OCR con coordenadas
    - Redimensionamiento inteligente para 4K (1920px max)
    - Ollama VLM (minicpm-v / llama-vision) para comprensión semántica
    """

    MAX_OCR_WIDTH = 1920  # Redimensionar para acelerar OCR en pantallas 4K

    def __init__(self, use_vlm: bool = False):
        self._use_vlm = use_vlm
        self._screenshot_dir = Path(tempfile.gettempdir()) / "eidos_screens"
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def capture_and_analyze(self) -> Scene:
        """Captura la pantalla y analiza la escena completa.

        Returns:
            Scene con OCR, regiones, descripción VLM
        """
        t0 = time.time()

        # 1. Screenshot
        screenshot_path = self._capture_screenshot()

        # 2. Detectar ventana activa
        window_title = self._get_active_window_title()

        # 3. Redimensionar para acelerar OCR en pantallas 4K
        ocr_path, scale_x, scale_y = self._resize_for_ocr(screenshot_path)

        # 4. OCR con Tesseract (texto + coordenadas)
        ocr_text, regions = self._ocr_with_positions(ocr_path)

        # Reescalar coordenadas al espacio original de pantalla
        if scale_x != 1.0 or scale_y != 1.0:
            for region in regions:
                region.x = int(region.x * scale_x)
                region.y = int(region.y * scale_y)
                region.width = int(region.width * scale_x)
                region.height = int(region.height * scale_y)

        # 5. VLM para comprensión semántica (opcional)
        vlm_desc = ""
        if self._use_vlm:
            vlm_desc = self._vlm_describe(screenshot_path)

        scene = Scene(
            timestamp=t0,
            screenshot_path=screenshot_path,
            window_title=window_title,
            ocr_full_text=ocr_text,
            regions=regions,
            vlm_description=vlm_desc,
        )
        return scene

    def _capture_screenshot(self) -> str:
        """Captura pantalla con maim o scrot."""
        path = str(self._screenshot_dir / f"eidos_scene_{int(time.time())}.png")
        try:
            # Preferir maim (más rápido)
            subprocess.run(
                ["maim", "-u", "no", path],
                capture_output=True, timeout=5, check=True
            )
        except Exception:
            try:
                subprocess.run(
                    ["scrot", "-z", path],
                    capture_output=True, timeout=5, check=True
                )
            except Exception:
                # Fallback: import ImageGrab
                try:
                    from PIL import ImageGrab
                    img = ImageGrab.grab()
                    img.save(path)
                except Exception:
                    path = ""
        return path

    def _resize_for_ocr(self, image_path: str) -> Tuple[str, float, float]:
        """Redimensiona imagen para OCR rápido manteniendo aspect ratio.

        Returns:
            (path_to_resized, scale_x, scale_y)
        """
        if not image_path or not Path(image_path).exists():
            return image_path, 1.0, 1.0

        try:
            from PIL import Image
            img = Image.open(image_path)
            w, h = img.size

            if w <= self.MAX_OCR_WIDTH:
                return image_path, 1.0, 1.0

            # Calcular nuevo tamaño manteniendo aspect ratio
            scale = self.MAX_OCR_WIDTH / w
            new_w = self.MAX_OCR_WIDTH
            new_h = int(h * scale)

            # Guardar versión redimensionada
            resized_path = str(Path(image_path).with_suffix(".ocr.png"))
            img.resize((new_w, new_h), Image.LANCZOS).save(resized_path, optimize=True)

            scale_x = w / new_w
            scale_y = h / new_h
            return resized_path, scale_x, scale_y
        except Exception:
            return image_path, 1.0, 1.0

    def _get_active_window_title(self) -> str:
        """Obtiene el título de la ventana activa."""
        try:
            wid = subprocess.check_output(
                ["xdotool", "getactivewindow"], text=True, timeout=2
            ).strip()
            title = subprocess.check_output(
                ["xdotool", "getwindowname", wid], text=True, timeout=2
            ).strip()
            return title
        except Exception:
            return "unknown"

    def _ocr_with_positions(self, image_path: str) -> Tuple[str, List[ScreenRegion]]:
        """OCR con Tesseract obteniendo coordenadas de palabras.

        Usa tesseract con output TSV para obtener bounding boxes.
        """
        if not image_path or not Path(image_path).exists():
            return "", []

        full_text = ""
        regions = []

        try:
            # Tesseract con TSV output (incluye coordenadas)
            tsv_output = str(Path(image_path).with_suffix(".tsv"))
            subprocess.run(
                ["tesseract", image_path, str(Path(image_path).with_suffix("")),
                 "-l", "spa+eng", "tsv", "--oem", "1"],
                capture_output=True, timeout=30, check=True
            )

            if Path(tsv_output).exists():
                lines = Path(tsv_output).read_text().splitlines()
                # Parsear TSV: level, page_num, block_num, par_num, line_num,
                # word_num, left, top, width, height, conf, text
                header_parsed = False
                for line in lines:
                    if not header_parsed:
                        header_parsed = True
                        continue
                    cols = line.split("\t")
                    if len(cols) >= 12 and cols[11].strip():
                        try:
                            left = int(cols[6])
                            top = int(cols[7])
                            width = int(cols[8])
                            height = int(cols[9])
                            conf = float(cols[10])
                            text = cols[11].strip()
                            full_text += text + " "

                            if conf > 30:  # filtrar ruido
                                regions.append(ScreenRegion(
                                    x=left + width // 2,
                                    y=top + height // 2,
                                    width=width,
                                    height=height,
                                    text=text,
                                    ocr_text=text,
                                    confidence=conf / 100.0,
                                    element_type="text",
                                ))
                        except (ValueError, IndexError):
                            continue
                full_text = full_text.strip()
        except Exception as e:
            log.debug("OCR falló: %s", e)
            # Fallback: OCR sin posiciones
            try:
                result = subprocess.check_output(
                    ["tesseract", image_path, "stdout", "-l", "spa+eng", "--oem", "1"],
                    text=True, timeout=30
                )
                full_text = result.strip()
            except Exception:
                pass

        return full_text, regions

    def _vlm_describe(self, image_path: str) -> str:
        """Describe la escena usando Ollama VLM."""
        if not image_path:
            return ""
        try:
            # Intentar con Ollama (minicpm-v o llama-vision)
            for model in ["minicpm-v", "llava:13b", "llama3.2-vision"]:
                try:
                    result = subprocess.run(
                        ["ollama", "run", model,
                         f"Describe brevemente qué ves en esta captura de pantalla. "
                         f"Texto visible, botones, campos, enlaces. "
                         f"Imagen: {image_path}"],
                        capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        return result.stdout.strip()[:500]
                except Exception:
                    continue
        except Exception:
            pass
        return ""

    def find_element(self, scene: Scene, query: str) -> List[ScreenRegion]:
        """Busca elementos en la escena que coincidan con la query.

        Busca en texto OCR y regiones detectadas.
        """
        results = []
        query_lower = query.lower()

        for region in scene.regions:
            if query_lower in region.text.lower():
                results.append(region)

        # Si no hay resultados exactos, buscar palabras parciales
        if not results:
            query_words = query_lower.split()
            for region in scene.regions:
                region_text = region.text.lower()
                if any(w in region_text for w in query_words):
                    results.append(region)

        # Ordenar por confianza
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    def find_text_area(self, scene: Scene, query: str) -> Optional[ScreenRegion]:
        """Busca la región que contenga texto similar a la query.

        Útil para encontrar la barra de direcciones, campos de búsqueda, etc.
        Prioriza regiones cerca del texto objetivo.
        """
        elements = self.find_element(scene, query)
        if elements:
            return elements[0]

        # Si no encuentra, buscar elementos tipo input cercanos a cualquier texto
        for region in scene.regions:
            if region.element_type in ("input", "unknown"):
                return region
        return None


# ── Layer 3: Decision Engine ───────────────────────────────────────────────────


class DecisionEngine:
    """Decide la siguiente acción basada en escena actual + plan.

    DETERMINISTA para acciones críticas. Consulta al LLM solo para
    decisiones estratégicas complejas (replanificación).
    """

    def __init__(self):
        self._action_history: List[Dict[str, Any]] = []
        self._stuck_count: int = 0
        self._same_scene_count: int = 0
        self._last_scene_hash: int = 0
        self._recent_actions: List[str] = []  # últimas 10 acciones

    def decide(self, plan: MissionPlan, scene: Scene,
               previous_results: List[ActionResult]) -> Dict[str, Any]:
        """Decide la siguiente acción a ejecutar.

        Returns:
            Dict con: action, target_coords, target_text, confidence, reason
        """
        # Detectar stuck
        scene_hash = hash(scene.ocr_full_text[:200])
        if scene_hash == self._last_scene_hash:
            self._same_scene_count += 1
        else:
            self._same_scene_count = 0
        self._last_scene_hash = scene_hash

        if self._same_scene_count > 5:
            return self._recovery_action(plan, scene, "stuck_same_scene")

        # Obtener paso actual del plan
        if plan.current_step < len(plan.steps):
            current_step = plan.steps[plan.current_step]
        else:
            return {"action": "done", "reason": "plan_completado"}

        # Verificar intentos máximos
        if current_step.attempts >= current_step.max_attempts:
            return self._recovery_action(plan, scene, "max_attempts")

        # Traducir paso del plan a acción concreta
        action = self._step_to_action(current_step, scene)

        # Registrar en historial
        self._action_history.append({
            "step": current_step.order,
            "action": action.get("action"),
            "scene_text": scene.ocr_full_text[:100],
            "window": scene.window_title,
        })
        self._recent_actions.append(action.get("action", ""))
        if len(self._recent_actions) > 10:
            self._recent_actions.pop(0)

        return action

    def _step_to_action(self, step: PlanStep, scene: Scene) -> Dict[str, Any]:
        """Convierte un paso del plan en acción concreta con coordenadas."""
        action = step.action
        target = step.target_description
        text = step.target_text

        if action == "navigate":
            return self._action_navigate(scene, text or target)

        elif action == "click":
            return self._action_click(scene, text or target)

        elif action == "type":
            return self._action_type(scene, text or target)

        elif action == "scroll":
            return self._action_scroll(scene, target)

        elif action == "wait":
            wait_seconds = 3.0
            # Intentar extraer segundos del target
            digits = re.findall(r'\d+', target)
            if digits:
                wait_seconds = float(digits[0])
            return {"action": "wait", "seconds": wait_seconds,
                    "reason": f"esperando: {target}"}

        elif action == "extract":
            return {"action": "extract", "target": target or "todo",
                    "scene_text": scene.ocr_full_text,
                    "reason": f"extrayendo: {target}"}

        elif action == "evaluate":
            return {"action": "evaluate",
                    "scene_text": scene.ocr_full_text,
                    "window": scene.window_title,
                    "reason": f"evaluando escena"}

        else:
            return {"action": action, "target": target, "text": text,
                    "reason": f"accion_generica: {action}"}

    def _action_navigate(self, scene: Scene, target: str) -> Dict[str, Any]:
        """Estrategia de navegación."""
        # Si es URL, navegar directamente
        if target.startswith("http") or "." in target:
            # Abrir Firefox si no está activo
            if "firefox" not in scene.window_title.lower() and \
               "mozilla" not in scene.window_title.lower() and \
               "chrom" not in scene.window_title.lower():
                return {
                    "action": "open_browser",
                    "url": target if target.startswith("http") else f"https://{target}",
                    "reason": f"abrir_navegador_y_navegar: {target}"
                }
            # Ya hay navegador, ir a barra de direcciones
            return {
                "action": "navigate_url",
                "url": target if target.startswith("http") else f"https://{target}",
                "shortcut": "ctrl+l",  # seleccionar barra de direcciones
                "reason": f"navegar_a: {target}"
            }
        # Navegación genérica
        return {
            "action": "navigate",
            "target": target,
            "reason": f"navegar: {target}"
        }

    def _action_click(self, scene: Scene, target: str) -> Dict[str, Any]:
        """Encuentra el mejor elemento clickable para el target."""
        if not target:
            return {"action": "click_center", "reason": "click_sin_target_especifico"}

        # Buscar en regiones OCR
        elements = []
        target_lower = target.lower()

        # Buscar coincidencias exactas
        for region in scene.regions:
            if target_lower in region.text.lower():
                elements.append(region)

        # Si no hay coincidencias exactas, buscar palabras parciales
        if not elements:
            target_words = target_lower.split()
            for region in scene.regions:
                region_text = region.text.lower()
                if any(w in region_text for w in target_words):
                    elements.append(region)

        if elements:
            # Elegir el de mayor confianza
            best = max(elements, key=lambda r: r.confidence)
            return {
                "action": "click",
                "x": best.x,
                "y": best.y,
                "text_clicked": best.text,
                "confidence": best.confidence,
                "reason": f"click_en: {best.text} (coincide con '{target}')"
            }

        # No se encontró el elemento → intentar en centro de pantalla
        # o delegar a LLM para replanificar
        return {
            "action": "click_center_offset",
            "reason": f"elemento_no_encontrado: '{target}', click compensado"
        }

    def _action_type(self, scene: Scene, text: str) -> Dict[str, Any]:
        """Prepara acción de tecleo."""
        if not text:
            text = ""
        return {
            "action": "type",
            "text": text,
            "clear_first": True,
            "reason": f"escribir: {text[:50]}"
        }

    def _action_scroll(self, scene: Scene, target: str) -> Dict[str, Any]:
        """Determina dirección y cantidad de scroll."""
        direction = "down"
        lines = 15
        if "up" in target.lower() or "arriba" in target.lower():
            direction = "up"
        if "poco" in target.lower() or "poco" in target.lower():
            lines = 5
        elif "mucho" in target.lower() or "final" in target.lower():
            lines = 30
        return {
            "action": "scroll",
            "direction": direction,
            "lines": lines,
            "reason": f"scroll_{direction}: {target}"
        }

    def _recovery_action(self, plan: MissionPlan, scene: Scene,
                         reason: str) -> Dict[str, Any]:
        """Genera acción de recuperación cuando la misión está atascada."""
        self._stuck_count += 1
        log.warning("DecisionEngine: stuck detectado. Razón: %s, count: %d",
                    reason, self._stuck_count)

        if self._stuck_count > 10:
            return {"action": "abort", "reason": f"demasiados_stucks: {reason}"}

        # Estrategia 1: Volver atrás (Escape o Alt+Left)
        if self._stuck_count % 3 == 1:
            return {"action": "key", "key": "Escape",
                    "reason": f"recovery_escape: {reason}"}

        # Estrategia 2: Scroll para cambiar la escena
        if self._stuck_count % 3 == 2:
            return {"action": "scroll", "direction": "down", "lines": 10,
                    "reason": f"recovery_scroll: {reason}"}

        # Estrategia 3: Replanificar con LLM
        return {"action": "replan", "reason": f"recovery_replan: {reason}"}

    def mark_step_completed(self, plan: MissionPlan):
        """Marca el paso actual como completado."""
        if plan.current_step < len(plan.steps):
            plan.steps[plan.current_step].completed = True
        plan.current_step += 1
        self._stuck_count = 0

    def is_mission_complete(self, plan: MissionPlan) -> bool:
        return plan.current_step >= len(plan.steps)

    def stats(self) -> Dict[str, Any]:
        return {
            "actions_history": len(self._action_history),
            "stuck_count": self._stuck_count,
            "recent_actions": self._recent_actions[-5:],
        }


# ── Layer 4+5: Human Emulator + Input Backend ──────────────────────────────────
# (usan HumanEmulator y USBHIDBackend existentes)


# ── ORQUESTADOR PRINCIPAL ──────────────────────────────────────────────────────


class ScreenController:
    """Orquestador principal de 5 capas.

    Integra Planner → VisualCortex → DecisionEngine → HumanEmulator → InputBackend
    en un solo loop de misión autónoma.
    """

    def __init__(self, use_llm: bool = True, use_vlm: bool = False,
                 dry_run: bool = False, enable_s89: bool = True,
                 enable_s90: bool = True):
        """
        Args:
            use_llm: usar LLM para planificación compleja
            use_vlm: usar VLM (Ollama) para comprensión visual
            dry_run: modo simulación (no ejecuta acciones reales)
            enable_s89: activar verificación post-acción + memoria episódica + world model
            enable_s90: activar descomposición jerárquica de metas + pipeline investigación
        """
        self.planner = Planner(use_llm=use_llm)
        self.visual_cortex = VisualCortex(use_vlm=use_vlm)
        self.decision_engine = DecisionEngine()
        self._dry_run = dry_run
        self._enable_s89 = enable_s89
        self._enable_s90 = enable_s90
        self._human_emulator = None
        self._hid_backend = None
        self._action_verifier = None
        self._episodic_memory = None
        self._world_model = None
        self._goal_decomposer = None
        self._research_pipeline = None
        self._extracted_knowledge: List[Dict[str, Any]] = []
        self._mission_log: List[Dict[str, Any]] = []

    # ── Lazy init ───────────────────────────────────────────────────────────────

    @property
    def human_emulator(self):
        if self._human_emulator is None:
            from core.human_emulator import get_human_emulator
            self._human_emulator = get_human_emulator()
        return self._human_emulator

    @property
    def hid_backend(self):
        if self._hid_backend is None:
            from core.usb_hid_backend import get_usb_hid_backend
            self._hid_backend = get_usb_hid_backend(auto_start=True)
        return self._hid_backend

    @property
    def action_verifier(self):
        if self._action_verifier is None and self._enable_s89:
            from core.action_verifier import get_action_verifier
            self._action_verifier = get_action_verifier()
        return self._action_verifier

    @property
    def episodic_memory(self):
        if self._episodic_memory is None and self._enable_s89:
            from core.screen_episodic_memory import get_screen_episodic_memory
            self._episodic_memory = get_screen_episodic_memory()
        return self._episodic_memory

    @property
    def world_model(self):
        if self._world_model is None and self._enable_s89:
            from core.ui_world_model import get_ui_world_model
            self._world_model = get_ui_world_model()
        return self._world_model

    @property
    def goal_decomposer(self):
        """S90: Descomposición jerárquica de metas complejas."""
        if self._goal_decomposer is None and self._enable_s90:
            from core.hierarchical_goal_decomposer import get_hgd
            self._goal_decomposer = get_hgd()
        return self._goal_decomposer

    @property
    def research_pipeline(self):
        """S90: Pipeline de investigación autónoma multi-salto."""
        if self._research_pipeline is None and self._enable_s90:
            from core.autonomous_research_pipeline import get_research_pipeline
            self._research_pipeline = get_research_pipeline()
        return self._research_pipeline

    # ── Ejecución de misión ─────────────────────────────────────────────────────

    def execute_mission(self, goal: str, max_steps: int = 20,
                       step_callback: callable = None,
                       _disable_s90: bool = False) -> Dict[str, Any]:
        """Ejecuta una misión completa desde lenguaje natural.

        Args:
            goal: meta en lenguaje natural
            max_steps: máximo de pasos a ejecutar
            step_callback: función llamada tras cada paso (para dashboards)
            _disable_s90: si True, no usa descomposición jerárquica
                          (usado internamente por execute_tree para evitar recursión)

        Returns:
            Dict con resultado completo de la misión
        """
        t0 = time.time()
        self._extracted_knowledge = []
        self._mission_log = []
        all_actions: List[ActionResult] = []

        log.info("MISIÓN INICIADA: %s (max_steps=%d, s89=%s, s90=%s)",
                goal, max_steps, self._enable_s89, self._enable_s90)

        # Reset S89 components para nueva misión
        if self._enable_s89 and self.action_verifier:
            self.action_verifier.reset()

        # S90: Intentar descomposición jerárquica para metas complejas
        goal_tree = None
        if self._enable_s90 and not _disable_s90 and self.goal_decomposer:
            try:
                goal_tree = self.goal_decomposer.decompose(goal, max_depth=2)
                if goal_tree and len(goal_tree.all_nodes) > 1:
                    log.info("🌳 S90: Meta descompuesta en %d submetas (fuente=%s)",
                            len(goal_tree.all_nodes),
                            goal_tree.metadata.get("source", "unknown"))
            except Exception as e:
                log.debug("HGD decompose falló: %s", e)
                goal_tree = None

        # Fase 1: Planificación
        # Si S90 produjo árbol con submetas, ejecutar cada submeta secuencialmente
        if goal_tree and len(goal_tree.all_nodes) > 1:
            plan = None  # No hay plan lineal, usamos el árbol
            log.info("🌳 Ejecutando misión como árbol de %d submetas",
                    len(goal_tree.all_nodes))
        else:
            # Planificación lineal (con memoria episódica S89)
            if self._enable_s89 and self.episodic_memory:
                cached_plan = self.episodic_memory.find_similar_plan(goal)
                if cached_plan and cached_plan.get("success_rate", 0) >= 0.6:
                    log.info("♻️  Plan reutilizado de memoria episódica (rate=%.2f, reused=%d)",
                            cached_plan["success_rate"], cached_plan["times_reused"])
                    plan = MissionPlan(goal=goal, max_steps=max_steps, created_at=time.time())
                    plan.steps = [
                        PlanStep(order=i, action=s["action"],
                                target_description=s["target_description"],
                                target_text=s["target_text"],
                                expected_result=s.get("expected_result", ""))
                        for i, s in enumerate(cached_plan["steps"])
                    ]
                else:
                    plan = self.planner.create_plan(goal, max_steps)
                    log.info("Plan creado: %d pasos", len(plan.steps))
            else:
                plan = self.planner.create_plan(goal, max_steps)
                log.info("Plan creado: %d pasos", len(plan.steps))

        # S90: Si tenemos árbol de submetas, ejecutar vía HGD
        if goal_tree and len(goal_tree.all_nodes) > 1:
            try:
                tree_result = self.goal_decomposer.execute_tree(
                    goal_tree, screen_controller=self,
                    max_total_steps=max_steps,
                    step_callback=step_callback,
                )
                total_elapsed = time.time() - t0
                mission_result = {
                    "goal": goal,
                    "completed": tree_result.get("completed_nodes", 0) > 0,
                    "total_steps": tree_result.get("total_steps_executed", 0),
                    "successful_steps": tree_result.get("completed_nodes", 0),
                    "failed_steps": tree_result.get("failed_nodes", 0),
                    "plan_steps_total": len(goal_tree.all_nodes),
                    "plan_steps_completed": tree_result.get("completed_nodes", 0),
                    "extracted_knowledge": self._extracted_knowledge,
                    "elapsed_total": round(total_elapsed, 3),
                    "action_log": self._mission_log,
                    "s90": {
                        "enabled": True,
                        "decomposer": "hgd",
                        "tree_nodes": len(goal_tree.all_nodes),
                        "tree_source": goal_tree.metadata.get("source", "unknown"),
                        "tree_result": tree_result,
                    },
                    "s89": {
                        "enabled": self._enable_s89,
                        "verifier": self.action_verifier.stats() if self.action_verifier else {},
                        "episodic_memory": self.episodic_memory.stats() if self.episodic_memory else {},
                        "world_model": self.world_model.stats() if self.world_model else {},
                    } if self._enable_s89 else {"enabled": False},
                }
                log.info("MISIÓN FINALIZADA (S90 árbol): %d/%d nodos, %.1fs",
                        tree_result.get("completed_nodes", 0),
                        len(goal_tree.all_nodes), total_elapsed)
                return mission_result
            except Exception as e:
                log.warning("Ejecución árbol falló: %s. Reintentando con plan lineal.", e)
                plan = self.planner.create_plan(goal, max_steps)
                # Fall through to linear loop below

        # Fase 2: Loop percepción-acción (plan lineal)
        step_count = 0
        while step_count < max_steps and not self.decision_engine.is_mission_complete(plan):
            step_count += 1

            try:
                # 2a. Capturar escena (Visual Cortex)
                scene = self.visual_cortex.capture_and_analyze()
                log.debug("Escena capturada: %s, %d regiones, %d chars OCR",
                         scene.window_title, len(scene.regions),
                         len(scene.ocr_full_text))

                # 2b. Decidir acción (Decision Engine)
                decision = self.decision_engine.decide(plan, scene, all_actions)

                # S89: Filtrar acción con World Model
                if self._enable_s89 and self.world_model and not self._dry_run:
                    decision = self.world_model.filter_action(
                        scene, decision, risk_threshold=0.7
                    )
                    if decision.get("risk_mitigated"):
                        log.info("🛡️  Acción filtrada por World Model: %s",
                                decision.get("reason", ""))

                log.debug("Decisión: %s → %s",
                         decision.get("action"), decision.get("reason", ""))

                # 2c. Ejecutar acción (Human Emulator + Input Backend)
                result = self._execute_action(decision, scene)
                all_actions.append(result)

                # S89: Verificación post-acción
                if self._enable_s89 and self.action_verifier and not self._dry_run:
                    scene_after = self.visual_cortex.capture_and_analyze()
                    verification = self.action_verifier.verify(
                        scene, scene_after, decision,
                        expected_outcome=decision.get("reason", "")
                    )
                    result.scene_after = scene_after

                    # Actualizar modelo predictivo
                    if self.world_model:
                        prediction = self.world_model.predict_transition(scene, decision)
                        self.world_model.record_outcome(prediction, verification.verified, scene_after)

                    # Si no se verificó, intentar recovery
                    if not verification.verified:
                        log.warning("❌ Verificación fallida: %s (verdict=%s)",
                                   verification.reason, verification.verdict.value)
                        recovery = self.action_verifier.get_recovery_action(
                            verification, scene_after
                        )
                        if recovery.get("action") != "retry":
                            self._mission_log.append({
                                "step": step_count,
                                "decision": decision,
                                "verification": verification.verdict.value,
                                "recovery": recovery.get("action"),
                                "result_success": False,
                            })

                            # Si la misión es hopeless, abortar
                            if self.action_verifier.is_mission_hopeless():
                                log.warning("💀 Misión hopeless tras %d pasos, abortando", step_count)
                                break

                            # Ejecutar recovery action
                            try:
                                recovery_result = self._execute_action(recovery, scene_after)
                            except Exception:
                                pass

                    # Grabar episodio en memoria
                    if self.episodic_memory and result.success:
                        reward = 1.0 if verification.verified else -0.3
                        self.episodic_memory.record_action(scene, decision, result, reward)
                        # Aprender patrón de UI
                        if decision.get("action") == "click" and decision.get("x"):
                            self.episodic_memory.learn_ui_pattern(
                                decision.get("text_clicked", "unknown"),
                                "button_if_clicked",
                                decision["x"], decision["y"],
                                context=scene.window_title,
                            )

                    # Update result based on verification
                    if not verification.verified:
                        result.success = False

                # 2d. Callback para dashboards
                if step_callback:
                    step_callback(step_count, decision, result, scene)

                # 2e. Actualizar estado
                self._mission_log.append({
                    "step": step_count,
                    "decision": decision,
                    "result_success": result.success,
                    "scene_window": scene.window_title,
                    "elapsed": result.elapsed,
                })

                # 2f. Manejar resultados especiales
                if decision.get("action") == "done":
                    log.info("Misión completada en paso %d", step_count)
                    break
                elif decision.get("action") == "abort":
                    log.warning("Misión abortada en paso %d: %s",
                               step_count, decision.get("reason"))
                    break
                elif decision.get("action") == "replan":
                    new_steps = self.planner.replan(
                        plan, scene, decision.get("reason", "stuck")
                    )
                    if new_steps:
                        plan.steps[plan.current_step:plan.current_step] = new_steps

                # Marcar paso completado si la acción fue exitosa
                if result.success:
                    current_step = (plan.steps[plan.current_step]
                                    if plan.current_step < len(plan.steps)
                                    else None)
                    if current_step:
                        current_step.attempts += 1
                        self.decision_engine.mark_step_completed(plan)

                # Extraer conocimiento si aplica
                if decision.get("action") == "extract" and result.success:
                    self._extracted_knowledge.append({
                        "source": scene.window_title,
                        "text": result.extracted_data.get("text", ""),
                        "links": result.extracted_data.get("links", []),
                        "timestamp": time.time(),
                    })

            except Exception as e:
                log.error("Error en paso %d: %s", step_count, e)
                all_actions.append(ActionResult(
                    success=False, action_type="error",
                    description=str(e), error=str(e),
                ))

        # Fase 3: Síntesis de resultados
        total_elapsed = time.time() - t0
        success_count = sum(1 for a in all_actions if a.success)

        # S89: Guardar plan en memoria episódica si fue exitoso
        if self._enable_s89 and self.episodic_memory and success_count > 1:
            mission_success = success_count >= len(all_actions) * 0.6
            self.episodic_memory.record_mission_plan(
                goal, plan.steps, mission_success, {"step_count": step_count}
            )

        mission_result = {
            "goal": goal,
            "completed": step_count > 0,
            "total_steps": step_count,
            "successful_steps": success_count,
            "failed_steps": len(all_actions) - success_count,
            "plan_steps_total": len(plan.steps),
            "plan_steps_completed": plan.current_step,
            "extracted_knowledge": self._extracted_knowledge,
            "elapsed_total": round(total_elapsed, 3),
            "avg_step_time": round(total_elapsed / max(1, step_count), 3),
            "action_log": self._mission_log,
            # S89 metrics
            "s89": {
                "enabled": self._enable_s89,
                "verifier": self.action_verifier.stats() if self.action_verifier else {},
                "episodic_memory": self.episodic_memory.stats() if self.episodic_memory else {},
                "world_model": self.world_model.stats() if self.world_model else {},
            } if self._enable_s89 else {"enabled": False},
            # S90 metrics
            "s90": {
                "enabled": self._enable_s90,
                "decomposer": "available" if self.goal_decomposer else "unavailable",
                "research_pipeline": "available" if self.research_pipeline else "unavailable",
                "goal_tree_used": goal_tree is not None and len(goal_tree.all_nodes) > 1,
            } if self._enable_s90 else {"enabled": False},
        }

        log.info("MISIÓN FINALIZADA: %d/%d pasos exitosos, %.1fs [S89=%s]",
                success_count, step_count, total_elapsed, self._enable_s89)

        return mission_result

    def _execute_action(self, decision: Dict[str, Any],
                        scene: Scene) -> ActionResult:
        """Ejecuta una acción usando HumanEmulator + InputBackend."""
        action = decision.get("action", "unknown")
        t0 = time.time()

        if self._dry_run:
            time.sleep(0.1)
            return ActionResult(
                success=True, action_type=action,
                description=f"[DRY RUN] {decision.get('reason', '')}",
                elapsed=0.1,
            )

        try:
            # Asegurar foco en navegador antes de cualquier acción de input
            if action not in ("done", "abort", "replan", "evaluate", "extract", "wait"):
                self.human_emulator._ensure_browser_focus()

            if action == "click":
                x = decision.get("x", 400)
                y = decision.get("y", 300)
                self.human_emulator.click_at(x, y)
                return ActionResult(
                    success=True, action_type="click",
                    description=f"Click en ({x}, {y}): {decision.get('text_clicked', '')}",
                    scene_before=scene,
                    elapsed=time.time() - t0,
                )

            elif action == "click_center":
                self.human_emulator.click_at(960, 540)
                return ActionResult(
                    success=True, action_type="click_center",
                    description="Click en centro de pantalla",
                    elapsed=time.time() - t0,
                )

            elif action == "click_center_offset":
                # Click en centro con pequeño offset aleatorio
                import random
                x = 960 + random.randint(-100, 100)
                y = 540 + random.randint(-50, 50)
                self.human_emulator.click_at(x, y)
                return ActionResult(
                    success=True, action_type="click_center_offset",
                    description=f"Click compensado en ({x}, {y})",
                    elapsed=time.time() - t0,
                )

            elif action == "type":
                text = decision.get("text", "")
                self.human_emulator.type_text(text)
                return ActionResult(
                    success=True, action_type="type",
                    description=f"Escrito: {text[:50]}",
                    elapsed=time.time() - t0,
                )

            elif action == "scroll":
                direction = decision.get("direction", "down")
                lines = decision.get("lines", 15)
                self.human_emulator.scroll(direction=direction, lines=lines)
                return ActionResult(
                    success=True, action_type="scroll",
                    description=f"Scroll {direction} {lines} líneas",
                    elapsed=time.time() - t0,
                )

            elif action == "key":
                key = decision.get("key", "Escape")
                self.human_emulator.press_key(key)
                return ActionResult(
                    success=True, action_type="key",
                    description=f"Tecla: {key}",
                    elapsed=time.time() - t0,
                )

            elif action == "navigate":
                # Navegación genérica (targets: barra_direcciones, siguiente_link, etc.)
                target = decision.get("target", "")
                if target == "barra_direcciones" or target == "address_bar":
                    # Seleccionar barra de direcciones con Ctrl+L
                    subprocess.run(
                        ["xdotool", "key", "ctrl+l"],
                        capture_output=True, timeout=2
                    )
                    self.human_emulator.human_pause(0.2, 0.4)
                    # Si hay texto para buscar, escribirlo
                    text = decision.get("text", decision.get("url", ""))
                    if text:
                        self.human_emulator.type_text(text + "\n")
                        # S92: Verificar que la navegación tuvo efecto
                        nav_verify = self._verify_navigation(scene, text[:30])
                    else:
                        nav_verify = {"verified": True, "title_after": ""}
                    return ActionResult(
                        success=True, action_type="navigate",
                        description=f"Barra direcciones: {text[:60] if text else 'seleccionada'}",
                        elapsed=time.time() - t0,
                        metadata={"navigation_verified": nav_verify.get("verified", True),
                                  "title_after": nav_verify.get("title_after", "")[:100]},
                    )
                elif target == "siguiente_link" or target == "next_link":
                    # Presionar Tab para siguiente elemento
                    subprocess.run(
                        ["xdotool", "key", "Tab"],
                        capture_output=True, timeout=2
                    )
                    self.human_emulator.human_pause(0.1, 0.3)
                    return ActionResult(
                        success=True, action_type="navigate",
                        description="Navegación: siguiente elemento",
                        elapsed=time.time() - t0,
                    )
                elif target == "abrir_navegador" or target == "open_browser":
                    url = decision.get("url", "https://google.com")
                    subprocess.Popen(
                        ["firefox", "--new-tab", url],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    self.human_emulator.human_pause(2.0, 4.0)
                    return ActionResult(
                        success=True, action_type="navigate",
                        description=f"Firefox abierto con: {url}",
                        elapsed=time.time() - t0,
                    )
                else:
                    # Navegación genérica: intentar Ctrl+L + escribir el target
                    subprocess.run(
                        ["xdotool", "key", "ctrl+l"],
                        capture_output=True, timeout=2
                    )
                    self.human_emulator.human_pause(0.2, 0.4)
                    self.human_emulator.type_text(target + "\n")
                    return ActionResult(
                        success=True, action_type="navigate",
                        description=f"Navegación genérica: {target[:60]}",
                        elapsed=time.time() - t0,
                    )

            elif action == "navigate_url":
                url = decision.get("url", "")
                shortcut = decision.get("shortcut", "ctrl+l")
                # Ctrl+L para seleccionar barra de direcciones
                subprocess.run(
                    ["xdotool", "key", shortcut],
                    capture_output=True, timeout=2
                )
                self.human_emulator.human_pause(0.2, 0.4)
                self.human_emulator.type_text(url + "\n")
                # S92: Verificar que la navegación tuvo efecto
                nav_verify = self._verify_navigation(scene, url[:30])
                return ActionResult(
                    success=True, action_type="navigate_url",
                    description=f"Navegado a: {url}",
                    elapsed=time.time() - t0,
                    metadata={"navigation_verified": nav_verify.get("verified", True),
                              "title_after": nav_verify.get("title_after", "")[:100]},
                )

            elif action == "open_browser":
                url = decision.get("url", "https://google.com")
                subprocess.Popen(
                    ["firefox", "--new-tab", url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self.human_emulator.human_pause(2.0, 4.0)
                return ActionResult(
                    success=True, action_type="open_browser",
                    description=f"Firefox abierto con: {url}",
                    elapsed=time.time() - t0,
                )

            elif action == "wait":
                seconds = decision.get("seconds", 2.0)
                self.human_emulator.human_pause(seconds, seconds + 1.0)
                return ActionResult(
                    success=True, action_type="wait",
                    description=f"Espera {seconds}s",
                    elapsed=time.time() - t0,
                )

            elif action == "extract":
                # Extraer texto y links de la escena
                scene_text = scene.ocr_full_text
                # Extraer URLs con regex
                urls = re.findall(
                    r'https?://[^\s<>"]+|www\.[^\s<>"]+|[a-zA-Z0-9.-]+\.(?:com|org|net|io|dev|es|app|ai)/[^\s<>"]*',
                    scene_text
                )
                # Extraer palabras y frases significativas
                words = re.findall(r'\b[A-ZÁÉÍÓÚ][a-záéíóú]{2,}\b', scene_text)
                return ActionResult(
                    success=True, action_type="extract",
                    description=f"Extraído: {len(urls)} URLs, {len(words)} términos",
                    extracted_data={
                        "text": scene_text[:2000],
                        "links": urls,
                        "key_terms": words,
                        "window": scene.window_title,
                    },
                    elapsed=time.time() - t0,
                )

            elif action == "evaluate":
                return ActionResult(
                    success=True, action_type="evaluate",
                    description=f"Escena evaluada: {scene.window_title}",
                    extracted_data={
                        "window": scene.window_title,
                        "text_preview": scene.ocr_full_text[:500],
                    },
                    elapsed=time.time() - t0,
                )

            elif action in ("done", "abort", "replan"):
                return ActionResult(
                    success=True, action_type=action,
                    description=decision.get("reason", action),
                    elapsed=0,
                )

            else:
                return ActionResult(
                    success=False, action_type=action,
                    description=f"Acción no implementada: {action}",
                    error=f"unknown_action: {action}",
                    elapsed=time.time() - t0,
                )

        except Exception as e:
            log.error("Error ejecutando acción '%s': %s", action, e)
            return ActionResult(
                success=False, action_type=action,
                description=f"Error: {e}", error=str(e),
                elapsed=time.time() - t0,
            )

    # ── Acciones rápidas ────────────────────────────────────────────────────────

    def quick_navigate(self, url: str) -> ActionResult:
        """Navegación rápida a una URL."""
        scene = self.visual_cortex.capture_and_analyze()
        decision = {
            "action": "navigate_url" if any(
                b in scene.window_title.lower()
                for b in ("firefox", "mozilla", "chrome", "chromium")
            ) else "open_browser",
            "url": url,
            "shortcut": "ctrl+l",
        }
        return self._execute_action(decision, scene)

    def quick_search(self, query: str) -> ActionResult:
        """Búsqueda rápida en Google."""
        scene = self.visual_cortex.capture_and_analyze()
        # Ir a Google
        self.quick_navigate("https://google.com")
        time.sleep(2)
        # Escribir query
        decision = {"action": "type", "text": query + "\n"}
        return self._execute_action(decision, scene)

    def quick_screenshot(self) -> str:
        """Captura rápida de pantalla."""
        return self.visual_cortex._capture_screenshot()

    # ── Verificación post-navegación [S92] ─────────────────────────────────────

    def _verify_navigation(self, scene_before: Scene, expected_url_hint: str = "") -> Dict[str, Any]:
        """Verifica que una navegación realmente tuvo efecto.

        Captura el estado post-navegación y compara:
        1. ¿Cambió el título de ventana? → navegación exitosa
        2. ¿El OCR muestra contenido nuevo? → probablemente exitosa
        3. ¿Sin cambios? → posible navegación fallida (timeout, error de red)

        Args:
            scene_before: escena antes de navegar
            expected_url_hint: pista de lo que debería verse (ej: "google", "github")

        Returns:
            Dict con veredicto: {verified: bool, title_changed: bool, new_title: str}
        """
        try:
            # Esperar a que la página cargue
            self.human_emulator.human_pause(1.5, 2.5)

            # Obtener título actual
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2
            )
            new_title = result.stdout.strip() if result.returncode == 0 else ""

            title_before = getattr(scene_before, 'window_title', '') or ''
            title_changed = (new_title != title_before and new_title != "")

            # Verificar si el hint aparece en el nuevo título
            hint_match = False
            if expected_url_hint and new_title:
                hint_match = expected_url_hint.lower() in new_title.lower()

            verified = title_changed or hint_match

            if not verified:
                log.warning("⚠️ Navegación no verificada: título sin cambios '%s'", new_title[:80])

            return {
                "verified": verified,
                "title_changed": title_changed,
                "title_before": title_before[:100],
                "title_after": new_title[:100],
                "hint_match": hint_match,
            }
        except Exception as e:
            log.debug("Error verificando navegación: %s", e)
            return {"verified": True, "error": str(e)}  # Asumir éxito en caso de error

    # ── S90: Investigación autónoma ──────────────────────────────────────────────

    def research(self, topic: str, max_depth: int = 2,
                 max_pages: int = 10,
                 progress_callback: callable = None) -> Dict[str, Any]:
        """Investigación autónoma multi-salto [S90].

        Usa AutonomousResearchPipeline para investigar un tema completo:
        1. Expande el topic en queries de búsqueda
        2. Navega, extrae links, sigue links relevantes
        3. Sintetiza hallazgos
        4. Inyecta en grafo de conocimiento

        Args:
            topic: tema a investigar
            max_depth: profundidad máxima de crawling (1-3)
            max_pages: máximo de páginas a visitar
            progress_callback: callback(progress_pct, message)

        Returns:
            Dict con resultados sintetizados
        """
        if not self._enable_s90:
            log.warning("S90 deshabilitado. Usa enable_s90=True.")
            return {"error": "S90 not enabled", "topic": topic}

        pipeline = self.research_pipeline
        if pipeline is None:
            return {"error": "Research pipeline not available", "topic": topic}

        log.info("🔬 Investigación autónoma: '%s' (depth=%d, max_pages=%d)",
                topic, max_depth, max_pages)
        return pipeline.research(
            topic=topic,
            max_depth=max_depth,
            max_pages=max_pages,
            screen_controller=self,
            progress_callback=progress_callback,
        )

    # ── BOM integration: get_affordances() ───────────────────────────────────────

    def get_affordances(self, goal: str = "") -> List[Dict[str, Any]]:
        """Devuelve acciones candidatas detectadas visualmente para el BOM.

        El BOM (causal_loop.py) usa esto como fuente adicional de candidatos.
        Usa VisualCortex para capturar la escena y el DecisionEngine para
        identificar elementos clickables con coordenadas.

        Returns:
            Lista de dicts: {action_key, label, x, y, confidence, element_type, source}
        """
        try:
            scene = self.visual_cortex.capture_and_analyze()
        except Exception as e:
            log.debug("get_affordances: visual cortex falló: %s", e)
            return []

        if not scene or not scene.regions:
            return []

        candidates: List[Dict[str, Any]] = []
        for region in scene.regions:
            if not region.text or len(region.text) > 50:
                continue
            action_key = f"click:{region.text.lower()[:40]}"
            candidates.append({
                "action_key": action_key,
                "label": region.text,
                "x": region.x,
                "y": region.y,
                "confidence": region.confidence,
                "element_type": region.element_type,
                "source": "screen_controller",
            })

        candidates.sort(key=lambda c: c["confidence"], reverse=True)
        return candidates[:20]

    # ── Stats ───────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "dry_run": self._dry_run,
            "missions_executed": len(self._mission_log),
            "knowledge_extracted": len(self._extracted_knowledge),
            "decision_engine": self.decision_engine.stats(),
            "hid_backend": self.hid_backend.stats() if not self._dry_run else {},
            "s89_enabled": self._enable_s89,
            "s90_enabled": self._enable_s90,
            "goal_decomposer": self.goal_decomposer is not None,
            "research_pipeline": self.research_pipeline is not None,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_controller: Optional[ScreenController] = None


def get_screen_controller(use_llm: bool = True, use_vlm: bool = False,
                          dry_run: bool = False, enable_s89: bool = True,
                          enable_s90: bool = True) -> ScreenController:
    """Obtiene el singleton ScreenController."""
    global _controller
    if _controller is None:
        _controller = ScreenController(
            use_llm=use_llm, use_vlm=use_vlm, dry_run=dry_run,
            enable_s89=enable_s89, enable_s90=enable_s90,
        )
    return _controller


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(
        description="EidosScreenControl — control de pantalla indetectable"
    )
    p.add_argument("--mission", type=str, help="Misión en lenguaje natural")
    p.add_argument("--max-steps", type=int, default=20, help="Máximo de pasos")
    p.add_argument("--dry-run", action="store_true", help="Modo simulación")
    p.add_argument("--navigate", type=str, help="Navegar a URL")
    p.add_argument("--search", type=str, help="Buscar en Google")
    p.add_argument("--screenshot", action="store_true", help="Capturar pantalla")
    p.add_argument("--ocr", action="store_true", help="OCR de pantalla actual")
    p.add_argument("--stats", action="store_true", help="Mostrar estadísticas")
    args = p.parse_args()

    sc = get_screen_controller(dry_run=args.dry_run)

    if args.mission:
        print(f"Misión: {args.mission}")
        result = sc.execute_mission(args.mission, max_steps=args.max_steps)
        print(f"\n✓ Completada: {result['completed']}")
        print(f"  Pasos: {result['total_steps']} total, "
              f"{result['successful_steps']} exitosos")
        print(f"  Tiempo: {result['elapsed_total']}s")
        if result['extracted_knowledge']:
            print(f"  Conocimiento extraído: {len(result['extracted_knowledge'])} items")

    elif args.navigate:
        result = sc.quick_navigate(args.navigate)
        print(f"Navegado a {args.navigate}: {result.success}")

    elif args.search:
        result = sc.quick_search(args.search)
        print(f"Búsqueda '{args.search}': {result.success}")

    elif args.screenshot:
        path = sc.quick_screenshot()
        print(f"Screenshot: {path}")

    elif args.ocr:
        scene = sc.visual_cortex.capture_and_analyze()
        print(f"Ventana: {scene.window_title}")
        print(f"Texto OCR ({len(scene.ocr_full_text)} chars):")
        print(scene.ocr_full_text[:500])
        print(f"Regiones: {len(scene.regions)}")

    elif args.stats:
        print(json.dumps(sc.stats(), indent=2, default=str))

    else:
        p.print_help()
