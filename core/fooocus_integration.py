"""
EIDOS Fooocus Integration
=========================
Wrapper para Stable Diffusion via Fooocus
Fooocus es un Stable Diffusion UI/API completo ubicado en external_repos/Fooocus

Capacidades:
- Text-to-Image de alta calidad
- Image-to-Image
- Inpainting
- Upscaling
- Múltiples estilos y modelos
"""

import os
import sys
import json
import time
import logging
from pathlib import Path
from typing import Optional, List, Dict
import subprocess
import requests

logger = logging.getLogger(__name__)

# Rutas
FOOOCUS_DIR = Path("/home/ser/EIDOS/external_repos/Fooocus")
OUTPUT_DIR = Path(os.path.expanduser("~/.eidos/images/generated"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class FooocusIntegration:
    """
    Wrapper para Fooocus (Stable Diffusion).

    Modos de uso:
    1. API mode: Fooocus corriendo como servidor
    2. CLI mode: Llamar a Fooocus directamente
    """

    def __init__(self, mode: str = "api", api_url: str = "http://localhost:7865"):
        self.mode = mode
        self.api_url = api_url
        self.server_process = None
        self.is_server_running = False

        if mode == "api":
            self._check_server()

    def _check_server(self) -> bool:
        """Verifica si el servidor Fooocus está corriendo"""
        try:
            response = requests.get(f"{self.api_url}/", timeout=2)
            self.is_server_running = (response.status_code == 200)
            return self.is_server_running
        except Exception:
            self.is_server_running = False
            return False

    def start_server(self, headless: bool = True) -> bool:
        """
        Inicia el servidor Fooocus en background.

        Args:
            headless: Si True, corre sin UI (solo API)

        Returns:
            True si se inició correctamente
        """
        if self.is_server_running:
            logger.info("Fooocus server ya está corriendo")
            return True

        if not FOOOCUS_DIR.exists():
            logger.error(f"Fooocus no encontrado en {FOOOCUS_DIR}")
            return False

        try:
            # Comando para iniciar Fooocus
            cmd = [
                sys.executable,  # python3
                str(FOOOCUS_DIR / "entry_with_update.py"),
                "--listen",  # Escuchar en todas las interfaces
                "--port", "7865"
            ]

            if headless:
                cmd.append("--headless")  # Sin UI, solo API

            logger.info(f"Iniciando Fooocus server: {' '.join(cmd)}")

            # Iniciar en background
            self.server_process = subprocess.Popen(
                cmd,
                cwd=str(FOOOCUS_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Esperar a que el servidor esté listo
            max_wait = 60  # segundos
            waited = 0
            while waited < max_wait:
                time.sleep(2)
                waited += 2
                if self._check_server():
                    logger.info("✅ Fooocus server iniciado correctamente")
                    return True

            logger.error("Timeout esperando Fooocus server")
            return False

        except Exception as e:
            logger.error(f"Error iniciando Fooocus server: {e}")
            return False

    def stop_server(self):
        """Detiene el servidor Fooocus"""
        if self.server_process:
            self.server_process.terminate()
            self.server_process.wait(timeout=10)
            self.is_server_running = False
            logger.info("Fooocus server detenido")

    def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        style: str = "Fooocus V2",
        width: int = 1024,
        height: int = 1024,
        num_images: int = 1,
        steps: int = 30,
        cfg_scale: float = 7.0,
        seed: int = -1
    ) -> List[str]:
        """
        Genera imágenes usando Fooocus API.

        Args:
            prompt: Descripción de la imagen
            negative_prompt: Qué evitar en la imagen
            style: Estilo predefinido de Fooocus
            width: Ancho en píxeles
            height: Alto en píxeles
            num_images: Número de imágenes a generar
            steps: Pasos de difusión (más = mejor calidad, más lento)
            cfg_scale: Qué tan fiel al prompt (7.0 es bueno)
            seed: Semilla aleatoria (-1 = aleatorio)

        Returns:
            Lista de rutas a las imágenes generadas
        """
        if self.mode == "api":
            return self._generate_api(
                prompt, negative_prompt, style, width, height,
                num_images, steps, cfg_scale, seed
            )
        else:
            return self._generate_cli(
                prompt, negative_prompt, style, width, height,
                num_images, steps, cfg_scale, seed
            )

    def _generate_api(
        self,
        prompt: str,
        negative_prompt: str,
        style: str,
        width: int,
        height: int,
        num_images: int,
        steps: int,
        cfg_scale: float,
        seed: int
    ) -> List[str]:
        """Genera vía API"""
        if not self.is_server_running:
            logger.warning("Fooocus server no está corriendo, intentando iniciar...")
            if not self.start_server():
                logger.error("No se pudo iniciar Fooocus server")
                return []

        try:
            # Payload para Fooocus API
            payload = {
                "prompt": prompt,
                "negative_prompt": negative_prompt or "ugly, blurry, low quality, distorted",
                "style_selections": [style],
                "performance_selection": "Speed",  # Speed, Quality, Extreme Speed
                "aspect_ratios_selection": f"{width}×{height}",
                "image_number": num_images,
                "sharpness": 2.0,
                "guidance_scale": cfg_scale,
                "base_model_name": "juggernautXL_v9Rundiffusion.safetensors",  # Modelo por defecto
                "refiner_model_name": "None",
                "refiner_switch": 0.5,
                "loras": [],
                "advanced_params": {
                    "disable_preview": False,
                    "adm_scaler_positive": 1.5,
                    "adm_scaler_negative": 0.8,
                    "adm_scaler_end": 0.3,
                    "adaptive_cfg": 7.0,
                    "sampler_name": "dpmpp_2m_sde_gpu",
                    "scheduler_name": "karras",
                    "overwrite_step": steps,
                    "overwrite_switch": -1,
                    "overwrite_width": width,
                    "overwrite_height": height,
                    "overwrite_vary_strength": -1,
                    "overwrite_upscale_strength": -1,
                    "mixing_image_prompt_and_vary_upscale": False,
                    "mixing_image_prompt_and_inpaint": False,
                    "debugging_cn_preprocessor": False,
                    "skipping_cn_preprocessor": False,
                    "canny_low_threshold": 64,
                    "canny_high_threshold": 128,
                    "refiner_swap_method": "joint",
                    "controlnet_softness": 0.25,
                    "freeu_enabled": False,
                    "freeu_b1": 1.01,
                    "freeu_b2": 1.02,
                    "freeu_s1": 0.99,
                    "freeu_s2": 0.95,
                    "debugging_inpaint_preprocessor": False,
                    "inpaint_disable_initial_latent": False,
                    "inpaint_engine": "v2.6",
                    "inpaint_strength": 1.0,
                    "inpaint_respective_field": 0.618,
                    "inpaint_mask_upload_checkbox": False,
                    "invert_mask_checkbox": False,
                    "inpaint_erode_or_dilate": 0
                },
                "require_base64": False,
                "async_process": False
            }

            if seed >= 0:
                payload["seed"] = seed

            # Hacer request a Fooocus API
            response = requests.post(
                f"{self.api_url}/v1/generation/text-to-image",
                json=payload,
                timeout=300  # 5 minutos max
            )

            if response.status_code == 200:
                result = response.json()

                # Fooocus devuelve rutas a las imágenes generadas
                image_paths = []
                for img_data in result.get("images", []):
                    # Copiar imagen a nuestro directorio
                    src_path = img_data.get("path")
                    if src_path and os.path.exists(src_path):
                        timestamp = int(time.time())
                        dest_filename = f"fooocus_{timestamp}_{len(image_paths)}.png"
                        dest_path = OUTPUT_DIR / dest_filename

                        import shutil
                        shutil.copy2(src_path, dest_path)
                        image_paths.append(str(dest_path))
                        logger.info(f"✅ Imagen generada: {dest_path}")

                return image_paths
            else:
                logger.error(f"Error API Fooocus: {response.status_code} - {response.text}")
                return []

        except Exception as e:
            logger.error(f"Error generando imagen: {e}")
            return []

    def _generate_cli(
        self,
        prompt: str,
        negative_prompt: str,
        style: str,
        width: int,
        height: int,
        num_images: int,
        steps: int,
        cfg_scale: float,
        seed: int
    ) -> List[str]:
        """Genera vía CLI (modo alternativo si API no funciona)"""
        logger.warning("Modo CLI no implementado aún, usa mode='api'")
        return []


# Singleton
_fooocus_integration = None

def get_fooocus() -> FooocusIntegration:
    """Get singleton FooocusIntegration instance"""
    global _fooocus_integration
    if _fooocus_integration is None:
        _fooocus_integration = FooocusIntegration(mode="api")
    return _fooocus_integration


# API conveniente
def generate_image(prompt: str, **kwargs) -> Optional[str]:
    """
    API simple para generar una imagen.

    Args:
        prompt: Descripción de la imagen
        **kwargs: Parámetros opcionales (style, width, height, etc.)

    Returns:
        Ruta a la primera imagen generada, o None si falla
    """
    fooocus = get_fooocus()
    images = fooocus.generate_image(prompt, **kwargs)

    if images:
        return images[0]
    return None


if __name__ == "__main__":
    # Test
    print("Fooocus Integration Test")
    print("=" * 50)

    fooocus = get_fooocus()

    # Test 1: Check server
    print(f"Server running: {fooocus.is_server_running}")

    # Test 2: Generate image (requiere Fooocus server corriendo)
    # prompt = "a beautiful mountain landscape at sunset, photorealistic, 4k"
    # images = fooocus.generate_image(prompt, num_images=1)
    # if images:
    #     print(f"✅ Generated: {images[0]}")
    # else:
    #     print("❌ Generation failed")

    print("\nPara usar Fooocus:")
    print("1. Inicia el servidor: python3 external_repos/Fooocus/entry_with_update.py --listen")
    print("2. O usa: fooocus.start_server()")
    print("3. Luego: generate_image('your prompt here')")
