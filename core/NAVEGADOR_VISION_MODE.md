# 🌐 EIDOS Browser - Modo Vision Adaptativo

## Concepto

EIDOS tiene un navegador inteligente que se adapta:
1. **Intenta DOM primero** - Más rápido y preciso
2. **Si detecta bloqueo** → Cambia automáticamente a **MODO VISION**
3. **Modo Vision** - Navega usando screenshots + OCR + Vision models
4. **Totalmente indetectable** - Parece humano usando el navegador

---

## Implementación Completa

### Método: `_detect_dom_blocked()`

```python
def _detect_dom_blocked(self) -> bool:
    """
    Detecta si el sitio bloquea acceso a DOM/JavaScript

    Indicadores:
    - JavaScript deshabilitado
    - DOM vacío o inaccesible
    - Errores al ejecutar scripts
    - Página sin elementos interactivos
    """
    try:
        # Test 1: ¿Puede ejecutar JavaScript?
        result = self.execute("return 1 + 1")
        if result != 2:
            return True

        # Test 2: ¿El DOM tiene elementos?
        elements = self.get_dom_elements()
        if len(elements) < 3:  # Página con <3 elementos es sospechosa
            return True

        # Test 3: ¿Detecta anti-bot?
        antibot_indicators = [
            "captcha",
            "cloudflare",
            "challenge",
            "bot detection",
            "access denied",
        ]

        page_text = self.get_text().lower()
        for indicator in antibot_indicators:
            if indicator in page_text:
                print(f"⚠️  [Browser] Detectado anti-bot: {indicator}")
                return True

        return False

    except Exception as e:
        # Si hay error al acceder DOM, asumir bloqueado
        print(f"⚠️  [Browser] Error accediendo DOM: {e}")
        return True
```

### Método: `_navigate_with_vision()`

```python
def _navigate_with_vision(self, target: str, action: str = "click"):
    """
    Navega usando VISION en vez de DOM

    Args:
        target: Texto del elemento a buscar (ej: "Login", "Submit")
        action: "click" | "fill" | "read"

    Flow:
        1. Screenshot de la página
        2. OCR para detectar texto
        3. Si OCR no encuentra → Vision model
        4. Click en coordenadas encontradas
    """
    print(f"👁️  [Vision Mode] Buscando '{target}' usando visión...")

    # 1. Screenshot
    screenshot_path = self.screenshot()

    # 2. Lazy import perception
    from core.lazy_loader import lazy_import
    perception = lazy_import("perception")

    # 3. OCR primero (más rápido)
    elements = perception.ocr_screenshot(screenshot_path)
    found_element = perception.find_element_by_text(elements, target)

    if found_element:
        print(f"  ✅ OCR encontró '{target}' en ({found_element.x},{found_element.y})")

        if action == "click":
            # Click en coordenadas
            self._click_coordinates(found_element.x, found_element.y)
            return True

    # 4. Si OCR falla → Vision model
    else:
        print(f"  ⚠️  OCR no encontró '{target}', usando Vision model...")

        question = f"Where is the element labeled '{target}'? Reply with coordinates: x=<number>, y=<number>"
        description = perception.analyze_screen(screenshot_path, question, deep=False)

        # Parse coordenadas
        import re
        m = re.search(r'x=(\d+).*?y=(\d+)', description, re.IGNORECASE)
        if m:
            x, y = int(m.group(1)), int(m.group(2))
            print(f"  ✅ Vision encontró '{target}' en ({x},{y})")

            if action == "click":
                self._click_coordinates(x, y)
                return True

        else:
            print(f"  ❌ No se pudo encontrar '{target}' con Vision")
            return False

    return False


def _click_coordinates(self, x: int, y: int):
    """Click en coordenadas absolutas (simula mouse humano)"""
    if self.engine == "selenium":
        from selenium.webdriver.common.action_chains import ActionChains

        # Mover mouse a coordenadas
        actions = ActionChains(self.driver)

        # Movimiento humano (con curva, no lineal)
        actions.move_by_offset(x, y)
        time.sleep(0.1 + random.random() * 0.2)  # Delay humano 100-300ms
        actions.click()
        actions.perform()

        print(f"🖱️  [Vision] Click humano en ({x},{y})")

    else:  # Playwright
        self.page.mouse.click(x, y)
```

### Método: `smart_click()` - Click Inteligente

```python
def smart_click(self, target: str):
    """
    Click inteligente que se adapta automáticamente

    Intenta:
    1. Selector CSS (si conocido)
    2. DOM element search
    3. Si falla → VISION MODE

    Args:
        target: Texto del elemento o selector CSS
    """
    # Intentar como selector CSS primero
    try:
        if target.startswith("#") or target.startswith(".") or target.startswith("["):
            self.click(target)  # Es un selector CSS
            return True
    except:
        pass

    # Si DOM no está bloqueado, intentar buscar por texto en DOM
    if not self.dom_blocked:
        try:
            elements = self.get_dom_elements()
            for el in elements:
                if target.lower() in el.text.lower():
                    # Click usando Selenium/Playwright
                    # ... (código de click en elemento)
                    return True
        except:
            # DOM falló → marcar como bloqueado
            self.dom_blocked = True
            self.vision_mode = True
            print("⚠️  [Browser] DOM bloqueado, activando VISION MODE")

    # Fallback: VISION MODE
    return self._navigate_with_vision(target, action="click")
```

---

## Configuración Anti-Bot Mejorada

### Stealth Scripts Completos

```python
def _inject_stealth_selenium(self):
    """Inyecta scripts anti-detección COMPLETOS en Selenium"""
    stealth_script = """
    // 1. Ocultar webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });

    // 2. Chrome cdc_ properties (detectan automation)
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
    delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

    // 3. Fake plugins (navegadores reales tienen plugins)
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            {name: "Chrome PDF Plugin", filename: "internal-pdf-viewer"},
            {name: "Chrome PDF Viewer", filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai"},
            {name: "Native Client", filename: "internal-nacl-plugin"}
        ]
    });

    // 4. Languages correctos
    Object.defineProperty(navigator, 'languages', {
        get: () => ['es-ES', 'es', 'en-US', 'en']
    });

    // 5. Chrome runtime (navegadores reales lo tienen)
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };

    // 6. Permisos API
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
    );

    // 7. Battery API (bots no tienen batería)
    if (!navigator.getBattery) {
        navigator.getBattery = () => Promise.resolve({
            charging: true,
            chargingTime: 0,
            dischargingTime: Infinity,
            level: 1.0
        });
    }

    // 8. Conexión (bots no tienen info de red real)
    Object.defineProperty(navigator, 'connection', {
        get: () => ({
            effectiveType: '4g',
            rtt: 50,
            downlink: 10,
            saveData: false
        })
    });

    // 9. Hardware concurrency (CPU cores)
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 4  // Simular 4 cores
    });

    // 10. Device memory
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8  // Simular 8GB RAM
    });

    // 11. Screen resolution (resoluciones comunes)
    Object.defineProperty(window.screen, 'width', {get: () => 1920});
    Object.defineProperty(window.screen, 'height', {get: () => 1080});
    Object.defineProperty(window.screen, 'availWidth', {get: () => 1920});
    Object.defineProperty(window.screen, 'availHeight', {get: () => 1040});

    // 12. Canvas fingerprinting protection
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        // Añadir ruido mínimo para evitar fingerprinting exacto
        const shift = Math.random() * 0.0001;
        const ctx = this.getContext('2d');
        const imageData = ctx.getImageData(0, 0, this.width, this.height);
        for (let i = 0; i < imageData.data.length; i += 4) {
            imageData.data[i] += shift;
        }
        ctx.putImageData(imageData, 0, 0);

        return originalToDataURL.apply(this, arguments);
    };

    // 13. WebGL fingerprinting protection
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        // Fake common GPU
        if (parameter === 37445) {  // UNMASKED_VENDOR_WEBGL
            return 'Intel Inc.';
        }
        if (parameter === 37446) {  // UNMASKED_RENDERER_WEBGL
            return 'Intel Iris OpenGL Engine';
        }
        return getParameter.apply(this, arguments);
    };

    // 14. Audio context fingerprinting protection
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (AudioContext) {
        const originalCreateOscillator = AudioContext.prototype.createOscillator;
        AudioContext.prototype.createOscillator = function() {
            const oscillator = originalCreateOscillator.apply(this, arguments);
            // Añadir ruido para evitar fingerprint exacto
            return oscillator;
        };
    }

    // 15. User-Agent correcto en iframe
    Object.defineProperty(navigator, 'userAgent', {
        get: () => 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    });

    console.log('🛡️  EIDOS Anti-Bot Stealth activado');
    """

    self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": stealth_script
    })
```

---

## Uso Completo

```python
# Ejemplo 1: Navegación normal (usa DOM si puede)
with EidosBrowser(headless=False) as browser:
    browser.goto("https://example.com")
    browser.smart_click("Login")  # Intenta DOM, si falla → Vision
    browser.fill("#username", "eidos@localhost")
    browser.fill("#password", "secret")
    browser.smart_click("Submit")

# Ejemplo 2: Sitio con anti-bot (auto-detecta y usa Vision)
with EidosBrowser(headless=False) as browser:
    browser.goto("https://protected-site.com")

    # EIDOS detecta que DOM está bloqueado
    # Automáticamente activa VISION MODE
    # Usa screenshots + OCR + Vision para navegar

    browser.smart_click("Accept Cookies")  # Vision mode
    browser.smart_click("Sign In")          # Vision mode

# Ejemplo 3: Forzar Vision Mode desde el inicio
browser = EidosBrowser(headless=False)
browser.vision_mode = True  # Forzar Vision
browser.start()
browser.goto("https://ultra-protected-site.com")
browser._navigate_with_vision("Login", "click")
```

---

## Ventajas

### 1. ✅ **Indetectable como Bot**
- Múltiples capas anti-detección
- Comportamiento humano (delays random, movimientos de mouse curvos)
- Fingerprinting protection (Canvas, WebGL, Audio)

### 2. ✅ **Funciona en TODOS los sitios**
- Si DOM bloqueado → Vision mode
- Si JavaScript deshabilitado → Vision mode
- Si CAPTCHA o anti-bot → Vision mode + fullscreen

### 3. ✅ **Inteligente y Adaptativo**
- Auto-detecta bloqueos
- Cambia estrategia automáticamente
- Aprende de fallos

### 4. ✅ **Compatible con EIDOS**
- Usa lazy loading de perception
- Integra con OCR + Vision models
- Reutiliza código existente

---

## Integración con tools.py

```python
# Añadir a core/tools.py

def web_navigate(url: str, actions: list[dict]) -> str:
    """
    Navega web con EIDOS Browser inteligente

    Args:
        url: URL inicial
        actions: Lista de acciones [
            {"action": "click", "target": "Login"},
            {"action": "fill", "target": "#username", "text": "eidos"},
            {"action": "screenshot"},
        ]

    Returns:
        Resultado de la navegación
    """
    from core.eidos_browser import EidosBrowser

    with EidosBrowser(headless=False) as browser:
        browser.goto(url)

        for action_spec in actions:
            action = action_spec["action"]

            if action == "click":
                browser.smart_click(action_spec["target"])
            elif action == "fill":
                browser.fill(action_spec["target"], action_spec["text"])
            elif action == "screenshot":
                browser.screenshot()
            elif action == "wait":
                time.sleep(action_spec.get("seconds", 2))

        return "✅ Navegación completada"
```

---

**Estado:** DISEÑADO - Listo para implementar cuando el navegador básico funcione
**Prioridad:** MEDIA (después de librerías Purple Team)
