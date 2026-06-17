"""
Visual-to-Code Generator (Kimi K2.5 inspired)
Convierte screenshots/imágenes de UI en código funcional
"""

import base64
import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import os
import sys

# Add EIDOS to path
EIDOS_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(EIDOS_ROOT))


@dataclass
class CodeGenerationResult:
    """Resultado de generación de código desde imagen"""
    framework: str  # html, react, python, vue, etc.
    code: str
    confidence: float
    detected_elements: List[Dict]
    reasoning: str
    metadata: Dict


class VisualCodeGenerator:
    """
    Generador de código desde imágenes visuales.
    
    Inspirado en Kimi K2.5 Visual Coding - convierte diseños UI
    en código funcional con alta fidelidad.
    """
    
    FRAMEWORKS = {
        "html": {
            "ext": ".html",
            "description": "HTML/CSS vanilla - mejor para prototipos rápidos",
            "priority": 1
        },
        "react": {
            "ext": ".jsx",
            "description": "React component - mejor para apps modernas",
            "priority": 2
        },
        "python_tkinter": {
            "ext": ".py",
            "description": "Python Tkinter - mejor para desktop apps",
            "priority": 3
        },
        "python_streamlit": {
            "ext": ".py",
            "description": "Streamlit - mejor para data apps",
            "priority": 4
        },
        "vue": {
            "ext": ".vue",
            "description": "Vue 3 component - alternativa moderna",
            "priority": 5
        }
    }
    
    def __init__(self):
        self.ollama_available = self._check_ollama()
        self.ui_parser = None
        self.clip_vision = None
        self._lazy_init()
    
    def _lazy_init(self):
        """Inicialización lazy de dependencias"""
        try:
            from core.ui_parser import OmniUIParser
            self.ui_parser = OmniUIParser()
        except Exception as e:
            print(f"⚠️ UI Parser no disponible: {e}")
        
        try:
            from core.clip_vision import CLIPVision
            self.clip_vision = CLIPVision()
        except Exception as e:
            print(f"⚠️ CLIP Vision no disponible: {e}")
    
    def _check_ollama(self) -> bool:
        """Verifica si Ollama está disponible"""
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def generate_from_screenshot(
        self,
        image_path: "str | Path",
        framework: str = "auto",
        prompt_hint: str = "",
        generate_variants: bool = False
    ) -> CodeGenerationResult:
        """
        Genera código desde una imagen/screenshot.
        
        Args:
            image_path: Ruta a la imagen
            framework: html, react, python_tkinter, python_streamlit, vue, auto
            prompt_hint: Contexto adicional (ej: "formulario de login")
            generate_variants: Si True, genera múltiples variantes
        
        Returns:
            CodeGenerationResult con código generado
        """
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Imagen no encontrada: {image_path}")
        
        # Paso 1: Analizar UI con OmniParser
        ui_elements = self._parse_ui(image_path)
        
        # Paso 2: Analizar contenido visual con CLIP
        visual_description = self._analyze_visual(image_path)
        
        # Paso 3: Seleccionar framework óptimo
        if framework == "auto":
            framework = self._select_framework(ui_elements, visual_description)
        
        # Paso 4: Generar código con LLM
        result = self._generate_code(
            image_path,
            ui_elements,
            visual_description,
            framework,
            prompt_hint
        )
        
        return result
    
    def _parse_ui(self, image_path: Path) -> List[Dict]:
        """Parsea elementos UI desde la imagen"""
        if not self.ui_parser:
            return []
        
        try:
            result = self.ui_parser.parse(str(image_path))
            return result.elements if hasattr(result, 'elements') else []
        except Exception as e:
            print(f"⚠️ Error parseando UI: {e}")
            return []
    
    def _analyze_visual(self, image_path: Path) -> str:
        """Analiza contenido visual con CLIP"""
        if not self.clip_vision:
            return ""
        
        try:
            # Usar CLIP para describir la imagen
            labels = [
                "login form", "dashboard", "data table", "navigation menu",
                "card layout", "modal dialog", "settings page", "profile page",
                "e-commerce product", "chart or graph", "landing page",
                "admin panel", "mobile app interface", "sidebar navigation"
            ]
            
            result = self.clip_vision.classify(str(image_path), labels)
            if result and hasattr(result, 'labels'):
                top_labels = [l for l, s in zip(result.labels, result.scores) if s > 0.5]
                return f"UI type detected: {', '.join(top_labels[:3])}"
            return ""
        except Exception as e:
            print(f"⚠️ Error analizando visual: {e}")
            return ""
    
    def _select_framework(self, ui_elements: List[Dict], visual_desc: str) -> str:
        """Selecciona el framework óptimo basado en el análisis"""
        # Heurísticas de selección
        element_types = [e.get('type', '') for e in ui_elements]
        
        # Si hay muchos elementos interactivos complejos → React
        interactive = ['button', 'input', 'dropdown', 'checkbox', 'slider']
        interactive_count = sum(1 for t in element_types if t in interactive)
        
        if interactive_count > 5 or 'dashboard' in visual_desc.lower():
            return 'react'
        
        # Si parece una app de datos → Streamlit
        if any(t in visual_desc.lower() for t in ['chart', 'graph', 'data', 'table']):
            return 'python_streamlit'
        
        # Por defecto → HTML (más simple y universal)
        return 'html'
    
    def _generate_code(
        self,
        image_path: Path,
        ui_elements: List[Dict],
        visual_desc: str,
        framework: str,
        prompt_hint: str
    ) -> CodeGenerationResult:
        """Genera código usando LLM"""
        
        if not self.ollama_available:
            # Fallback: generar código básico basado en elementos detectados
            return self._generate_basic_code(ui_elements, framework)
        
        # Preparar imagen para Ollama
        with open(image_path, 'rb') as f:
            image_b64 = base64.b64encode(f.read()).decode()
        
        # Construir prompt
        elements_desc = self._describe_elements(ui_elements)
        
        system_prompt = f"""You are an expert UI developer. Convert the provided screenshot into clean, functional {framework} code.

Requirements:
1. Match the visual design as closely as possible
2. Use appropriate colors, spacing, and typography
3. Make it responsive where applicable
4. Include all interactive elements (buttons, inputs, etc.)
5. Add proper styling

Detected UI elements: {elements_desc}
Visual analysis: {visual_desc}
Context: {prompt_hint}

Output ONLY the code, no explanations."""

        # Llamar a Ollama con visión
        try:
            import requests
            
            response = requests.post(
                'http://localhost:11434/api/generate',
                json={
                    'model': 'moondream:latest',
                    'prompt': system_prompt,
                    'images': [image_b64],
                    'stream': False,
                    'options': {'temperature': 0.3}
                },
                timeout=120
            )
            
            if response.status_code == 200:
                code = response.json().get('response', '').strip()
                # Limpiar markdown si existe
                code = self._clean_code(code)
                
                return CodeGenerationResult(
                    framework=framework,
                    code=code,
                    confidence=0.85,
                    detected_elements=ui_elements,
                    reasoning=f"Generated {framework} based on {len(ui_elements)} UI elements detected",
                    metadata={'model': 'moondream:latest', 'source': 'ollama'}
                )
        except Exception as e:
            print(f"⚠️ Error con Ollama: {e}")
        
        # Fallback
        return self._generate_basic_code(ui_elements, framework)
    
    def _generate_basic_code(self, ui_elements: List[Dict], framework: str) -> CodeGenerationResult:
        """Genera código básico sin LLM"""
        
        if framework == 'html':
            code = self._generate_html_basic(ui_elements)
        elif framework == 'react':
            code = self._generate_react_basic(ui_elements)
        elif framework == 'python_tkinter':
            code = self._generate_tkinter_basic(ui_elements)
        elif framework == 'python_streamlit':
            code = self._generate_streamlit_basic(ui_elements)
        else:
            code = self._generate_html_basic(ui_elements)
        
        return CodeGenerationResult(
            framework=framework,
            code=code,
            confidence=0.6,
            detected_elements=ui_elements,
            reasoning=f"Basic {framework} template generated from {len(ui_elements)} detected elements",
            metadata={'source': 'template', 'note': 'LLM not available, using templates'}
        )
    
    def _generate_html_basic(self, elements: List[Dict]) -> str:
        """Genera HTML básico desde elementos"""
        html_parts = [
            "<!DOCTYPE html>",
            "<html lang='en'>",
            "<head>",
            "    <meta charset='UTF-8'>",
            "    <meta name='viewport' content='width=device-width, initial-scale=1.0'>",
            "    <title>Generated UI</title>",
            "    <style>",
            "        body { font-family: system-ui, sans-serif; margin: 20px; background: #f5f5f5; }",
            "        .container { max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }",
            "        button { background: #007bff; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; }",
            "        button:hover { background: #0056b3; }",
            "        input, select { padding: 8px; border: 1px solid #ddd; border-radius: 4px; margin: 5px 0; }",
            "    </style>",
            "</head>",
            "<body>",
            "    <div class='container'>"
        ]
        
        for elem in elements:
            elem_type = elem.get('type', '')
            text = elem.get('text', '')
            
            if elem_type == 'button':
                html_parts.append(f"        <button>{text or 'Button'}</button>")
            elif elem_type == 'input':
                html_parts.append(f"        <input type='text' placeholder='{text or 'Enter text'}' />")
            elif elem_type == 'checkbox':
                html_parts.append(f"        <label><input type='checkbox' /> {text or 'Option'}</label>")
            elif elem_type == 'text' or elem_type == 'label':
                html_parts.append(f"        <p>{text}</p>")
            elif elem_type == 'heading':
                html_parts.append(f"        <h2>{text or 'Heading'}</h2>")
        
        html_parts.extend([
            "    </div>",
            "</body>",
            "</html>"
        ])
        
        return '\n'.join(html_parts)
    
    def _generate_react_basic(self, elements: List[Dict]) -> str:
        """Genera componente React básico"""
        jsx_elements = []
        
        for elem in elements:
            elem_type = elem.get('type', '')
            text = elem.get('text', '')
            
            if elem_type == 'button':
                jsx_elements.append(f"      <button className=\"btn\">{text or 'Button'}</button>")
            elif elem_type == 'input':
                jsx_elements.append(f"      <input className=\"input\" type=\"text\" placeholder=\"{text or 'Enter text'}\" />")
            elif elem_type == 'checkbox':
                jsx_elements.append(f"      <label className=\"checkbox\"><input type=\"checkbox\" /> {text or 'Option'}</label>")
            elif elem_type == 'text' or elem_type == 'label':
                jsx_elements.append(f"      <p className=\"text\">{text}</p>")
            elif elem_type == 'heading':
                jsx_elements.append(f"      <h2 className=\"heading\">{text or 'Heading'}</h2>")
        
        jsx_code = '\n'.join(jsx_elements)
        
        return f"""import React from 'react';
import './Component.css';

const GeneratedComponent = () => {{
  return (
    <div className="container">
{jsx_code}
    </div>
  );
}};

export default GeneratedComponent;"""
    
    def _generate_tkinter_basic(self, elements: List[Dict]) -> str:
        """Genera código Tkinter básico"""
        code_lines = [
            "import tkinter as tk",
            "from tkinter import ttk",
            "",
            "def main():",
            "    root = tk.Tk()",
            "    root.title('Generated App')",
            "    root.geometry('400x300')",
            "",
            "    # Widgets"
        ]
        
        for i, elem in enumerate(elements):
            elem_type = elem.get('type', '')
            text = elem.get('text', '')
            
            if elem_type == 'button':
                code_lines.append(f"    btn_{i} = ttk.Button(root, text='{text or 'Button'}')")
                code_lines.append(f"    btn_{i}.pack(pady=5)")
            elif elem_type == 'input':
                code_lines.append(f"    entry_{i} = ttk.Entry(root)")
                code_lines.append(f"    entry_{i}.pack(pady=5)")
            elif elem_type == 'checkbox':
                code_lines.append(f"    chk_{i} = tk.BooleanVar()")
                code_lines.append(f"    ttk.Checkbutton(root, text='{text or 'Option'}', variable=chk_{i}).pack(pady=5)")
            elif elem_type == 'text' or elem_type == 'label':
                code_lines.append(f"    ttk.Label(root, text='{text or 'Label'}').pack(pady=5)")
        
        code_lines.extend([
            "",
            "    root.mainloop()",
            "",
            "if __name__ == '__main__':",
            "    main()"
        ])
        
        return '\n'.join(code_lines)
    
    def _generate_streamlit_basic(self, elements: List[Dict]) -> str:
        """Genera código Streamlit básico"""
        code_lines = [
            "import streamlit as st",
            "",
            "st.title('Generated App')",
            ""
        ]
        
        for elem in elements:
            elem_type = elem.get('type', '')
            text = elem.get('text', '')
            
            if elem_type == 'button':
                code_lines.append(f"if st.button('{text or 'Button'}'):")
                code_lines.append("    st.write('Button clicked!')")
            elif elem_type == 'input':
                code_lines.append(f"user_input = st.text_input('{text or 'Enter text'}')")
            elif elem_type == 'checkbox':
                code_lines.append(f"checked = st.checkbox('{text or 'Option'}')")
            elif elem_type == 'text' or elem_type == 'label':
                code_lines.append(f"st.write('{text}')")
            elif elem_type == 'heading':
                code_lines.append(f"st.header('{text or 'Header'}')")
        
        return '\n'.join(code_lines)
    
    def _describe_elements(self, elements: List[Dict]) -> str:
        """Describe elementos para el prompt"""
        if not elements:
            return "No UI elements detected"
        
        counts = {}
        for e in elements:
            t = e.get('type', 'unknown')
            counts[t] = counts.get(t, 0) + 1
        
        return ', '.join(f"{n} {t}" for t, n in counts.items())
    
    def _clean_code(self, code: str) -> str:
        """Limpia código de markdown y otros artefactos"""
        # Remover bloques markdown
        code = re.sub(r'^```\w*\n?', '', code)
        code = re.sub(r'\n?```$', '', code)
        
        # Remover explicaciones comunes
        lines = code.split('\n')
        result = []
        in_code = False
        
        for line in lines:
            # Detectar inicio de código
            if any(line.strip().startswith(x) for x in ['import ', 'from ', '<', 'function', 'const', 'let', 'var', 'def ', 'class ']):
                in_code = True
            
            if in_code or line.strip().startswith(('<', 'import', 'from', 'def', 'class')):
                result.append(line)
        
        return '\n'.join(result) if result else code
    
    def save_code(self, result: CodeGenerationResult, output_dir: Optional[str] = None) -> Path:
        """Guarda el código generado en archivo"""
        if output_dir is None:
            out_path = Path.home() / '.eidos' / 'generated_code'
        else:
            out_path = Path(output_dir)

        out_path.mkdir(parents=True, exist_ok=True)

        ext = self.FRAMEWORKS.get(result.framework, {}).get('ext', '.txt')
        filename = f"generated_{result.framework}_{int(time.time())}{ext}"
        filepath = out_path / filename
        
        with open(filepath, 'w') as f:
            f.write(result.code)
        
        # Guardar metadata
        meta_path = filepath.with_suffix('.json')
        with open(meta_path, 'w') as f:
            json.dump({
                'framework': result.framework,
                'confidence': result.confidence,
                'detected_elements': result.detected_elements,
                'reasoning': result.reasoning,
                'metadata': result.metadata
            }, f, indent=2)
        
        return filepath


# ─── CLI / Test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    
    print("=" * 60)
    print("Visual-to-Code Generator (Kimi K2.5 inspired)")
    print("=" * 60)
    
    if len(sys.argv) < 2:
        print("\nUso: python visual_code_generator.py <imagen.png> [framework]")
        print("\nFrameworks disponibles:")
        for fw, info in VisualCodeGenerator.FRAMEWORKS.items():
            print(f"  - {fw}: {info['description']}")
        print("\nEjemplo:")
        print("  python visual_code_generator.py screenshot.png react")
        sys.exit(1)
    
    image_path = sys.argv[1]
    framework = sys.argv[2] if len(sys.argv) > 2 else "auto"
    
    print(f"\n📸 Analizando: {image_path}")
    print(f"🎯 Framework: {framework}")
    
    generator = VisualCodeGenerator()
    
    try:
        result = generator.generate_from_screenshot(image_path, framework)
        
        print(f"\n✅ Código generado!")
        print(f"   Framework: {result.framework}")
        print(f"   Confianza: {result.confidence:.1%}")
        print(f"   Elementos detectados: {len(result.detected_elements)}")
        print(f"   Razonamiento: {result.reasoning}")
        
        # Guardar
        filepath = generator.save_code(result)
        print(f"\n💾 Guardado en: {filepath}")
        
        # Preview
        print(f"\n📄 Preview (primeras 30 líneas):")
        print("-" * 40)
        lines = result.code.split('\n')[:30]
        for line in lines:
            print(line)
        if len(result.code.split('\n')) > 30:
            print("...")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
