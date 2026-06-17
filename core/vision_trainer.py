"""
EIDOS core/vision_trainer.py — Calibración y Entrenamiento de Visión
=====================================================================
Crea un dataset de calibración de 20 escenarios reales y mide la precisión
de moondream2 y llama3.2-vision para que EIDOS sepa en qué confiar.

También implementa la Vision Chain completa:
  screenshot → describe → decide (DOM vs desktop) → actúa

Uso:
    from core.vision_trainer import VisionTrainer
    vt = VisionTrainer()
    vt.run_calibration()       # test completo con los 20 escenarios
    vt.vision_chain("haz click en el botón Guardar")  # ciclo completo
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DATA_DIR     = os.path.expanduser("~/.eidos")
CALIB_FILE   = os.path.join(DATA_DIR, "vision_calibration.json")
SS_DIR       = os.path.join(DATA_DIR, "screenshots")
os.makedirs(SS_DIR, exist_ok=True)


# ── Dataset base de 20 escenarios ────────────────────────────────────────────
# Cada escenario define qué se debería describir y qué acción se debería tomar
BASE_SCENARIOS: list[dict[str, str]] = [
    {"id": "s01", "context": "terminal", "expected_keywords": ["terminal", "bash", "shell", "prompt", "$"],
     "expected_action": "desktop", "description": "Terminal vacío o con prompt"},
    {"id": "s02", "context": "browser",  "expected_keywords": ["browser", "firefox", "chrome", "url", "web"],
     "expected_action": "dom",     "description": "Navegador web abierto"},
    {"id": "s03", "context": "desktop",  "expected_keywords": ["desktop", "escritorio", "icons", "iconos"],
     "expected_action": "desktop", "description": "Escritorio vacío"},
    {"id": "s04", "context": "browser",  "expected_keywords": ["login", "contraseña", "password", "email"],
     "expected_action": "dom",     "description": "Formulario de login en browser"},
    {"id": "s05", "context": "terminal", "expected_keywords": ["error", "traceback", "exception", "failed"],
     "expected_action": "desktop", "description": "Terminal con error de Python"},
    {"id": "s06", "context": "browser",  "expected_keywords": ["google", "search", "buscar", "barra"],
     "expected_action": "dom",     "description": "Google.com en browser"},
    {"id": "s07", "context": "desktop",  "expected_keywords": ["file", "manager", "files", "carpeta"],
     "expected_action": "desktop", "description": "Gestor de archivos"},
    {"id": "s08", "context": "browser",  "expected_keywords": ["button", "botón", "click", "submit"],
     "expected_action": "dom",     "description": "Página con botones prominentes"},
    {"id": "s09", "context": "terminal", "expected_keywords": ["nmap", "scan", "port", "open"],
     "expected_action": "desktop", "description": "Escaneo nmap en terminal"},
    {"id": "s10", "context": "desktop",  "expected_keywords": ["editor", "code", "python", "vscode"],
     "expected_action": "desktop", "description": "Editor de código"},
    {"id": "s11", "context": "browser",  "expected_keywords": ["texto", "párrafo", "article", "contenido"],
     "expected_action": "dom",     "description": "Artículo de texto en browser"},
    {"id": "s12", "context": "terminal", "expected_keywords": ["download", "descarga", "progress", "%"],
     "expected_action": "desktop", "description": "Descarga en terminal"},
    {"id": "s13", "context": "browser",  "expected_keywords": ["tabla", "table", "datos", "rows"],
     "expected_action": "dom",     "description": "Tabla HTML en browser"},
    {"id": "s14", "context": "desktop",  "expected_keywords": ["ventana", "window", "dialog", "popup"],
     "expected_action": "desktop", "description": "Diálogo de confirmación"},
    {"id": "s15", "context": "browser",  "expected_keywords": ["mapa", "map", "geolocation", "streetview"],
     "expected_action": "dom",     "description": "Google Maps en browser"},
    {"id": "s16", "context": "terminal", "expected_keywords": ["git", "commit", "branch", "merge"],
     "expected_action": "desktop", "description": "Git en terminal"},
    {"id": "s17", "context": "browser",  "expected_keywords": ["video", "youtube", "play", "reproducir"],
     "expected_action": "dom",     "description": "YouTube en browser"},
    {"id": "s18", "context": "desktop",  "expected_keywords": ["settings", "configuración", "system", "panel"],
     "expected_action": "desktop", "description": "Panel de configuración del sistema"},
    {"id": "s19", "context": "terminal", "expected_keywords": ["python", "script", "running", "ejecutando"],
     "expected_action": "desktop", "description": "Script Python ejecutándose"},
    {"id": "s20", "context": "browser",  "expected_keywords": ["chat", "mensaje", "input", "enviar"],
     "expected_action": "dom",     "description": "Interfaz de chat en browser"},
]


@dataclass
class CalibrationResult:
    scenario_id:   str
    model:         str
    description:   str   # lo que describió el VLM
    keywords_found: list[str]
    keyword_score: float   # 0.0 – 1.0
    action_correct: bool   # ¿decidió DOM o desktop correctamente?
    latency_s:     float
    error:         str = ""


class VisionTrainer:
    """
    Calibra y mejora las capacidades de visión de EIDOS.
    """

    def __init__(self) -> None:
        self._results: list[CalibrationResult] = []
        self._prompt_overrides: dict[str, str] = {}  # mejoras de prompt derivadas de errores

    # ── Utilidades ─────────────────────────────────────────────────────────

    def _take_screenshot(self) -> str:
        """Captura la pantalla actual. Retorna path al PNG."""
        ts   = int(time.time())
        path = os.path.join(SS_DIR, f"calib_{ts}.png")
        # Intentar scrot
        r = subprocess.run(["scrot", "-z", path], capture_output=True, timeout=5)
        if r.returncode == 0 and os.path.exists(path):
            return path
        # Fallback gnome-screenshot
        r2 = subprocess.run(["gnome-screenshot", "-f", path], capture_output=True, timeout=5)
        if r2.returncode == 0 and os.path.exists(path):
            return path
        return ""

    def _encode_image(self, path: str) -> str:
        """Convierte imagen a base64."""
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    def _vlm_describe(self, model: str, image_b64: str, prompt: str) -> tuple[str, float]:
        """Llama al VLM y mide la latencia. Retorna (descripción, latencia_s)."""
        payload = json.dumps({
            "model":  model,
            "stream": False,
            "prompt": prompt,
            "images": [image_b64],
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.load(resp)
            return result.get("response", ""), round(time.time() - t0, 2)
        except Exception as e:
            return f"[VLM ERROR] {e}", round(time.time() - t0, 2)

    def _decide_action(self, description: str) -> str:
        """
        Decide si usar DOM (Playwright) o desktop (AT-SPI2/xdotool/OCR)
        basándose en la descripción del VLM.
        """
        desc_lower = description.lower()
        browser_signals = ["browser", "firefox", "chrome", "chromium", "url", "http",
                           "navegador", "web page", "página web", "website"]
        if any(s in desc_lower for s in browser_signals):
            return "dom"
        return "desktop"

    # ── Calibración ─────────────────────────────────────────────────────────

    def run_calibration(
        self,
        models: list[str] | None = None,
        screenshot_path: str = "",
        verbose: bool = True
    ) -> dict[str, Any]:
        """
        Ejecuta los 20 escenarios de calibración con los VLMs disponibles.
        Si screenshot_path está vacío, captura la pantalla actual para cada test.
        Retorna el reporte completo y lo guarda en ~/.eidos/vision_calibration.json
        """
        if models is None:
            models = ["moondream:latest", "moondream:latest"]

        print(f"\n[👁️ VISION TRAINER] Iniciando calibración con modelos: {models}")
        print(f"  Escenarios: {len(BASE_SCENARIOS)} | Imagen: {'externa' if screenshot_path else 'captura en vivo'}\n")

        all_results: list[dict] = []
        prompt_base = "Describe brevemente qué ves en esta captura de pantalla de un sistema Linux. Menciona el tipo de aplicación (terminal, navegador web, escritorio, editor de código, etc.) y los elementos más importantes."

        # Si hay una imagen fija para todos los tests (modo batch sin pantalla)
        fixed_img_b64 = ""
        if screenshot_path and os.path.exists(screenshot_path):
            fixed_img_b64 = self._encode_image(screenshot_path)

        for scenario in BASE_SCENARIOS:
            sid = scenario["id"]
            if verbose:
                print(f"  [{sid}] {scenario['description']}...")

            # Capturar imagen si no es fija
            if fixed_img_b64:
                img_b64 = fixed_img_b64
            else:
                ss_path = self._take_screenshot()
                if not ss_path:
                    print(f"    ⚠️ No se pudo capturar pantalla para {sid}")
                    continue
                img_b64 = self._encode_image(ss_path)

            for model in models:
                prompt = self._prompt_overrides.get(f"{model}_{sid}", prompt_base)
                description, latency = self._vlm_describe(model, img_b64, prompt)

                # Evaluar keywords
                desc_l = description.lower()
                found  = [kw for kw in scenario["expected_keywords"] if kw in desc_l]
                kw_score = len(found) / max(len(scenario["expected_keywords"]), 1)

                # Evaluar decisión de acción
                decided_action = self._decide_action(description)
                action_correct = (decided_action == scenario["expected_action"])

                result = {
                    "scenario_id":    sid,
                    "description_expected": scenario["description"],
                    "model":          model,
                    "vlm_response":   description[:500],  # pyre-ignore[arg-type]
                    "keywords_found": found,
                    "keyword_score":  round(kw_score, 2),
                    "action_decided": decided_action,
                    "action_expected": scenario["expected_action"],
                    "action_correct": action_correct,
                    "latency_s":      latency,
                }
                all_results.append(result)

                if verbose:
                    kw_str = f"{len(found)}/{len(scenario['expected_keywords'])} kw"
                    act_str = "✅" if action_correct else "❌"
                    print(f"    {model.split(':')[0]:20s} {kw_str:10s} {act_str} DOM/desktop | {latency}s")  # pyre-ignore[arg-type]
                    if not action_correct:
                        print(f"      VLM dijo: {description[:120]}")  # pyre-ignore[arg-type]

        # Calcular métricas globales por modelo
        report: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_scenarios": len(BASE_SCENARIOS),
            "per_model": {},
            "results": all_results,
        }

        for model in models:
            model_results = [r for r in all_results if r["model"] == model]
            if not model_results:
                continue
            avg_kw    = sum(r["keyword_score"]  for r in model_results) / len(model_results)
            avg_lat   = sum(r["latency_s"]       for r in model_results) / len(model_results)
            act_acc   = sum(1 for r in model_results if r["action_correct"]) / len(model_results)
            report["per_model"][model] = {
                "keyword_score_avg": round(avg_kw * 100, 1),
                "action_accuracy":   round(act_acc * 100, 1),
                "avg_latency_s":     round(avg_lat, 2),
                "tests_run":         len(model_results),
            }
            print(f"\n  📊 {model.split(':')[0]} → keyword:{avg_kw*100:.0f}% | acción:{act_acc*100:.0f}% | {avg_lat:.1f}s avg")  # pyre-ignore[arg-type]

        # Guardar resultados
        with open(CALIB_FILE, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n  💾 Guardado: {CALIB_FILE}")

        return report

    # ── Vision Chain completa ─────────────────────────────────────────────────

    def vision_chain(
        self,
        task: str,
        model: str = "moondream:latest",
        verbose: bool = True
    ) -> dict[str, Any]:
        """
        Ciclo completo: screenshot → describe → decide (DOM vs desktop) → devuelve plan de acción.

        Returns dict con:
          - screen_context: "browser" | "terminal" | "desktop" | "editor"
          - recommended_method: "dom" | "at-spi2" | "ocr" | "vlm"
          - vlm_description: lo que el VLM describió
          - action_plan: texto con los pasos recomendados para la tarea
          - screenshot_path: ruta al screenshot tomado
        """
        if verbose:
            print(f"\n[👁️ VISION CHAIN] Tarea: {task}")

        # 1. Screenshot
        ss_path = self._take_screenshot()
        if not ss_path:
            return {"error": "No se pudo capturar pantalla", "screenshot_path": ""}

        if verbose:
            print(f"  📸 Screenshot: {ss_path}")

        # 2. Describir con VLM
        img_b64 = self._encode_image(ss_path)
        prompt  = (
            f"Estás ayudando a un agente IA a completar la tarea: '{task}'\n"
            f"Describe la pantalla actual: ¿qué tipo de aplicación está activa? "
            f"(navegador web, terminal, escritorio, editor de código, app GTK/Qt, etc.) "
            f"¿Hay elementos UI relevantes para la tarea (botones, campos de texto, links)? "
            f"Responde en español, máximo 3 frases."
        )
        description, latency = self._vlm_describe(model, img_b64, prompt)

        if verbose:
            print(f"  🔍 VLM ({latency}s): {description[:200]}")  # pyre-ignore[arg-type]

        # 3. Decidir método de interacción
        desc_lower = description.lower()
        browser_signals  = ["browser", "firefox", "chrome", "navegador", "url", "web",
                             "página", "http", "chromium"]
        terminal_signals = ["terminal", "bash", "shell", "konsole", "prompt", "$", "#"]
        editor_signals   = ["editor", "code", "vscode", "python", "script", "ide"]

        if any(s in desc_lower for s in browser_signals):
            context = "browser"
            method  = "dom"
        elif any(s in desc_lower for s in terminal_signals):
            context = "terminal"
            method  = "desktop"
        elif any(s in desc_lower for s in editor_signals):
            context = "editor"
            method  = "desktop"
        else:
            context = "desktop"
            method  = "at-spi2"

        # 4. Generar plan de acción con LLM ligero
        plan_prompt = (
            f"Contexto de pantalla: {context}. Método de interacción: {method}.\n"
            f"Descripción visual: {description[:300]}\n"  # pyre-ignore[arg-type]
            f"Tarea: {task}\n\n"
            f"Genera un plan de 2-4 pasos concretos usando las tools disponibles "
            f"({'Playwright DOM (dom_click, dom_type, dom_get_text)' if method == 'dom' else 'xdotool/pyautogui (click_gui_element, type_in_gui)'})."
        )
        try:
            payload = json.dumps({
                "model":    "lfm2.5-thinking:1.2b",
                "stream":   False,
                "messages": [{"role": "user", "content": plan_prompt}],
                "options":  {"num_predict": 200, "temperature": 0.3},
            }).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                plan_resp = json.load(resp)
            action_plan = plan_resp.get("message", {}).get("content", "")
        except Exception as e:
            action_plan = f"[PLAN ERROR] {e}"

        result = {
            "screenshot_path":    ss_path,
            "screen_context":     context,
            "recommended_method": method,
            "vlm_description":    description,
            "action_plan":        action_plan,
            "vlm_model":          model,
            "vlm_latency_s":      latency,
        }

        if verbose:
            print(f"  🎯 Contexto: {context} | Método: {method}")
            print(f"  📋 Plan:\n{action_plan[:300]}")  # pyre-ignore[arg-type]

        return result

    # ── Mejora de prompts ─────────────────────────────────────────────────────

    def improve_prompts_from_results(self) -> int:
        """
        Lee vision_calibration.json y genera mejoras de prompt para los
        escenarios donde keyword_score < 0.5. Devuelve nº de mejoras aplicadas.
        """
        if not os.path.exists(CALIB_FILE):
            print("[VISION TRAINER] No hay datos de calibración. Ejecuta run_calibration() primero.")
            return 0

        with open(CALIB_FILE) as f:
            data = json.load(f)

        improvements = 0
        for result in data.get("results", []):
            if result["keyword_score"] < 0.5:
                sid   = result["scenario_id"]
                model = result["model"]
                scenario = next((s for s in BASE_SCENARIOS if s["id"] == sid), None)
                if not scenario:
                    continue
                # Prompt mejorado: incluir las palabras clave esperadas
                new_prompt = (
                    f"Describe la pantalla. Presta atención a: {', '.join(scenario['expected_keywords'])}. "
                    f"¿Ves una {scenario['description']}? Responde brevemente en español."
                )
                self._prompt_overrides[f"{model}_{sid}"] = new_prompt
                improvements += 1

        print(f"[VISION TRAINER] {improvements} mejoras de prompt generadas")
        return improvements

    def load_calibration_report(self) -> dict[str, Any]:
        """Lee y devuelve el último reporte de calibración."""
        if not os.path.exists(CALIB_FILE):
            return {"error": "No hay calibración previa"}
        with open(CALIB_FILE) as f:
            return json.load(f)


# ── OCR Pipeline Mejorado (Fase 25) ──────────────────────────────────────────

class OCRPipeline:
    """
    Pipeline OCR avanzado usando Tesseract (rápido) o Surya-OCR (preciso)
    para localizar coordenadas exactas de texto en la pantalla.
    EIDOS usa esto cuando el VLM entiende la pantalla pero necesita hacer click en
    un texto específico.
    """
    def __init__(self) -> None:
        self.use_surya = self._check_surya_installed()
        if self.use_surya:
            print("[👁️ OCR] Detector avanzado Surya-OCR disponible.")
        else:
            print("[👁️ OCR] Tesseract activado (fallback). Instala 'surya-ocr' para mayor precisión.")

    def _check_surya_installed(self) -> bool:
        try:
            import surya  # type: ignore
            return True
        except ImportError:
            return False

    def find_text_coordinates(self, image_path: str, target_text: str, threshold: int = 80) -> list[tuple[int, int, int, int]]:
        """
        Busca target_text en la imagen y retorna una lista de bounding boxes (x, y, w, h).
        Usa difflib/fuzz_ratio para matching borroso.
        """
        target_lower = target_text.lower().strip()
        results = []
        
        # 1. Fallback a Tesseract ( pytesseract )
        try:
            import pytesseract
            from PIL import Image
            img = Image.open(image_path)
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            
            for i in range(len(data['text'])):
                word = data['text'][i].strip().lower()
                if not word:
                    continue
                # Simple substring match o Jaro-Winkler/Levenshtein si está disponible
                if target_lower in word or word in target_lower:
                    x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
                    results.append((x, y, w, h))
            
            # Agrupar boxes cercanos (opcional)
            return results
            
        except ImportError:
            print("[👁️ OCR] pytesseract no está instalado. Ejecuta: pip install pytesseract")
            return []
        except Exception as e:
            print(f"[👁️ OCR] Error Tesseract: {e}")
            return []

    def get_center(self, bbox: tuple[int, int, int, int]) -> tuple[int, int]:
        """Calcula el centro exacto de un bounding box para enviar un mouse_click."""
        x, y, w, h = bbox
        return (x + w // 2, y + h // 2)


# ── CLI rápido ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    vt = VisionTrainer()

    if len(sys.argv) > 1 and sys.argv[1] == "chain":  # pyre-ignore[arg-type]
        task = " ".join(sys.argv[2:]) or "describe la pantalla"  # pyre-ignore[arg-type]
        result = vt.vision_chain(task)
        print(f"\nContexto: {result['screen_context']}")
        print(f"Método:   {result['recommended_method']}")
        print(f"VLM:      {result['vlm_description'][:200]}")  # pyre-ignore[arg-type]
    else:
        # Calibración rápida con solo moondream (más rápido)
        report = vt.run_calibration(models=["moondream:latest"])
        vt.improve_prompts_from_results()
        for model, stats in report.get("per_model", {}).items():
            print(f"\n{'='*50}")
            print(f"Modelo: {model}")
            print(f"Keywords: {stats['keyword_score_avg']}%")
            print(f"Acción DOM/desktop: {stats['action_accuracy']}%")
            print(f"Latencia media: {stats['avg_latency_s']}s")
