"""
EIDOS Auto-Installer — Sistema de auto-instalación de dependencias
Sin limitaciones - EIDOS instala lo que necesite automáticamente

Para que EIDOS sea completamente autónomo sin ayuda humana
"""
import subprocess
import sys
import os
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PackageInfo:
    """Información de un paquete"""
    name: str
    manager: str  # "pip", "apt", "npm", "cargo"
    installed: bool
    version: Optional[str] = None


class AutoInstaller:
    """
    Sistema de auto-instalación de dependencias

    Capabilities:
    - Detecta imports faltantes en código Python
    - Busca paquetes en todos los package managers
    - Auto-instala sin preguntar (modo autónomo)
    - Aprende mappings de import → package
    """

    # Mapping común de import → package name (cuando difieren)
    IMPORT_TO_PACKAGE = {
        # Visión y procesamiento de imágenes
        'cv2': 'opencv-python',
        'PIL': 'pillow',
        'skimage': 'scikit-image',

        # ML/AI
        'sklearn': 'scikit-learn',
        'torch': 'torch',
        'tensorflow': 'tensorflow',
        'tf': 'tensorflow',
        'transformers': 'transformers',
        'sentence_transformers': 'sentence-transformers',

        # Datos
        'numpy': 'numpy',
        'np': 'numpy',
        'pandas': 'pandas',
        'pd': 'pandas',
        'scipy': 'scipy',
        'matplotlib': 'matplotlib',
        'seaborn': 'seaborn',

        # Web scraping/automation
        'bs4': 'beautifulsoup4',
        'selenium': 'selenium',
        'playwright': 'playwright',
        'scrapy': 'scrapy',
        'lxml': 'lxml',

        # HTTP clients
        'requests': 'requests',
        'aiohttp': 'aiohttp',
        'httpx': 'httpx',
        'urllib3': 'urllib3',

        # Async
        'asyncio': None,  # Built-in
        'aiofiles': 'aiofiles',

        # Web frameworks
        'fastapi': 'fastapi',
        'flask': 'flask',
        'django': 'django',
        'starlette': 'starlette',

        # Databases
        'sqlalchemy': 'sqlalchemy',
        'pymongo': 'pymongo',
        'redis': 'redis',
        'psycopg2': 'psycopg2-binary',
        'mysqldb': 'mysqlclient',

        # Config/ENV
        'yaml': 'pyyaml',
        'toml': 'toml',
        'dotenv': 'python-dotenv',
        'configparser': None,  # Built-in

        # Crypto/Security
        'cryptography': 'cryptography',
        'jwt': 'pyjwt',
        'bcrypt': 'bcrypt',
        'hashlib': None,  # Built-in

        # Testing
        'pytest': 'pytest',
        'unittest': None,  # Built-in
        'mock': None,  # Built-in in unittest.mock

        # Utils
        'pydantic': 'pydantic',
        'tqdm': 'tqdm',
        'click': 'click',
        'typer': 'typer',
        'rich': 'rich',
        'colorama': 'colorama',

        # Date/Time
        'dateutil': 'python-dateutil',
        'pytz': 'pytz',
        'arrow': 'arrow',
        'datetime': None,  # Built-in

        # JSON/Serialization
        'json': None,  # Built-in
        'pickle': None,  # Built-in
        'msgpack': 'msgpack',

        # NLP
        'nltk': 'nltk',
        'spacy': 'spacy',
        'gensim': 'gensim',

        # PDF/Office
        'PyPDF2': 'PyPDF2',
        'pdf2image': 'pdf2image',
        'openpyxl': 'openpyxl',
        'docx': 'python-docx',

        # System
        'psutil': 'psutil',
        'sh': 'sh',
        'subprocess': None,  # Built-in
        'os': None,  # Built-in
        'sys': None,  # Built-in
        'pathlib': None,  # Built-in
    }

    # Librerías core que EIDOS DEBE tener instaladas
    CORE_LIBRARIES = {
        'python': [
            'numpy', 'pandas', 'scipy', 'matplotlib', 'seaborn',
            'requests', 'aiohttp', 'httpx',
            'beautifulsoup4', 'selenium', 'playwright',
            'pillow', 'opencv-python',
            'torch', 'transformers',
            'scikit-learn',
            'fastapi', 'flask', 'django',
            'pytest', 'black', 'mypy', 'ruff',
            'pydantic', 'sqlalchemy',
            'redis', 'celery',
        ],
        'system': [
            'build-essential', 'git', 'curl', 'wget',
            'python3-dev', 'python3-pip',
            'nodejs', 'npm',
            'rustc', 'cargo',
            'golang-go',
            'tesseract-ocr', 'tesseract-ocr-spa',
            'chromium-driver',
            'nmap', 'gobuster', 'sqlmap',
            'lynis', 'clamav',
        ],
        'npm': [
            'typescript', 'prettier', 'eslint',
            'webpack', 'vite',
            'react', 'vue', 'svelte',
            'express', 'fastify',
        ],
        'cargo': [
            'ripgrep', 'fd-find', 'bat', 'exa',
            'tokio', 'serde',
        ],
    }

    def __init__(self):
        self.installed_cache = {}
        print("🤖 [Auto-Installer] Sistema inicializado")

    def is_installed(self, package: str, manager: str = "pip") -> bool:
        """Verifica si un paquete está instalado"""
        cache_key = f"{manager}:{package}"
        if cache_key in self.installed_cache:
            return self.installed_cache[cache_key]

        try:
            if manager == "pip":
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "show", package],
                    capture_output=True,
                    timeout=5
                )
                installed = result.returncode == 0

            elif manager == "apt":
                result = subprocess.run(
                    ["dpkg", "-l", package],
                    capture_output=True,
                    timeout=5
                )
                installed = result.returncode == 0

            elif manager == "npm":
                result = subprocess.run(
                    ["npm", "list", "-g", package],
                    capture_output=True,
                    timeout=5
                )
                installed = result.returncode == 0

            elif manager == "cargo":
                result = subprocess.run(
                    ["cargo", "install", "--list"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                installed = package in result.stdout

            else:
                installed = False

            self.installed_cache[cache_key] = installed
            return installed

        except Exception:
            return False

    def install(self, package: str, manager: str = "pip", force: bool = False) -> Tuple[bool, str]:
        """
        Instala un paquete automáticamente

        Args:
            package: Nombre del paquete
            manager: "pip" | "apt" | "npm" | "cargo"
            force: Si True, reinstala aunque ya esté instalado

        Returns:
            (success, message)
        """
        if not force and self.is_installed(package, manager):
            return True, f"[SKIP] {package} ya instalado"

        print(f"📦 [Auto-Installer] Instalando {package} via {manager}...")

        try:
            if manager == "pip":
                cmd = [sys.executable, "-m", "pip", "install", package, "-q"]

            elif manager == "apt":
                cmd = ["sudo", "apt-get", "install", "-y", package]

            elif manager == "npm":
                cmd = ["npm", "install", "-g", package]

            elif manager == "cargo":
                cmd = ["cargo", "install", package]

            else:
                return False, f"[ERROR] Package manager desconocido: {manager}"

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 min max por paquete
            )

            if result.returncode == 0:
                # Invalidar cache
                self.installed_cache[f"{manager}:{package}"] = True
                print(f"  ✅ {package} instalado exitosamente")
                return True, f"[OK] {package} instalado"
            else:
                error = result.stderr[:200]
                print(f"  ❌ Error instalando {package}: {error}")
                return False, f"[ERROR] {error}"

        except subprocess.TimeoutExpired:
            return False, f"[TIMEOUT] Instalación de {package} excedió 5 minutos"
        except Exception as e:
            return False, f"[ERROR] {e}"

    def install_missing_import(self, import_name: str) -> Tuple[bool, str]:
        """
        Auto-instala un módulo Python faltante

        Detecta el package correcto y lo instala automáticamente
        """
        # Primero verificar si ya está instalado
        try:
            __import__(import_name)
            return True, f"[OK] {import_name} ya disponible"
        except ImportError:
            pass

        # Mapear import → package name
        package_name = self.IMPORT_TO_PACKAGE.get(import_name, import_name)

        print(f"🔍 [Auto-Installer] Import faltante detectado: {import_name}")
        print(f"   Mapeando a package: {package_name}")

        # Intentar instalación
        success, msg = self.install(package_name, manager="pip")

        if success:
            # Verificar que ahora sí funcione
            try:
                __import__(import_name)
                return True, f"[AUTO-FIXED] {import_name} instalado y funcional"
            except ImportError as e:
                return False, f"[PARTIAL] Instalado pero import falló: {e}"

        return success, msg

    def install_all_core_libraries(self) -> Dict[str, List[str]]:
        """
        Instala TODAS las librerías core que EIDOS necesita

        Para hacerlo completamente autónomo sin ayuda humana
        """
        print("\n╔═══════════════════════════════════════════════════════════════╗")
        print("║   AUTO-INSTALLER: Instalando todas las librerías core       ║")
        print("╚═══════════════════════════════════════════════════════════════╝\n")

        results = {
            'python': [],
            'system': [],
            'npm': [],
            'cargo': [],
        }

        # Python libraries
        print("\n🐍 PYTHON LIBRARIES:")
        for lib in self.CORE_LIBRARIES['python']:
            success, msg = self.install(lib, manager="pip")
            if success:
                results['python'].append(lib)

        # System packages (apt)
        print("\n🖥️  SYSTEM PACKAGES:")
        for pkg in self.CORE_LIBRARIES['system']:
            success, msg = self.install(pkg, manager="apt")
            if success:
                results['system'].append(pkg)

        # NPM packages
        print("\n📦 NPM PACKAGES:")
        for pkg in self.CORE_LIBRARIES['npm']:
            success, msg = self.install(pkg, manager="npm")
            if success:
                results['npm'].append(pkg)

        # Cargo packages
        print("\n🦀 CARGO PACKAGES:")
        for pkg in self.CORE_LIBRARIES['cargo']:
            success, msg = self.install(pkg, manager="cargo")
            if success:
                results['cargo'].append(pkg)

        print("\n╔═══════════════════════════════════════════════════════════════╗")
        print("║   INSTALACIÓN COMPLETADA                                     ║")
        print("╚═══════════════════════════════════════════════════════════════╝")
        print(f"\n  Python: {len(results['python'])}/{len(self.CORE_LIBRARIES['python'])}")
        print(f"  System: {len(results['system'])}/{len(self.CORE_LIBRARIES['system'])}")
        print(f"  NPM:    {len(results['npm'])}/{len(self.CORE_LIBRARIES['npm'])}")
        print(f"  Cargo:  {len(results['cargo'])}/{len(self.CORE_LIBRARIES['cargo'])}")

        return results


# Singleton
auto_installer = AutoInstaller()


# Helper functions
def install_if_missing(package: str, manager: str = "pip") -> bool:
    """Helper: Instala paquete si falta"""
    return auto_installer.install(package, manager)[0]


def fix_import_error(import_name: str) -> bool:
    """Helper: Auto-fix ModuleNotFoundError"""
    return auto_installer.install_missing_import(import_name)[0]


# Test
if __name__ == "__main__":
    print("=== Test Auto-Installer ===\n")

    # Test 1: Check installed
    print("Test 1: Verificar paquete instalado")
    installed = auto_installer.is_installed("requests", "pip")
    print(f"  requests installed: {installed}\n")

    # Test 2: Install missing import
    print("Test 2: Auto-instalar import faltante")
    success, msg = auto_installer.install_missing_import("yaml")
    print(f"  {msg}\n")

    print("✅ Auto-Installer funcional")
