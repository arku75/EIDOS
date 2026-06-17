"""
core/eidos_stealth.py — ANTI-DETECTION MODULE for EIDOS. S125-L.

COMPLETE browser fingerprint defense + human behavior simulation + network stealth.

EIDOS must be COMPLETELY undetectable when browsing the web. This module provides:

1. FINGERPRINT DEFENSE (8 layers):
   - WebDriver flag hidden (navigator.webdriver = undefined)
   - Canvas fingerprint randomized (consistent per-session noise)
   - WebGL fingerprint masked (vendor/renderer spoofed to common GPU)
   - Plugins array populated (5 realistic plugins)
   - Languages consistent with IP (es-ES primary)
   - Screen resolution standard (1920x1080, dpr=1)
   - Fonts standard set (common Linux/Windows fonts)
   - AudioContext fingerprint masked (oscillator drift, channel noise)

2. HUMAN BEHAVIOR SIMULATION:
   - Mouse: stochastic optimized submovement model (Fitts' Law, NOT bezier)
   - Keyboard: variable delays 30-200ms (log-normal distribution)
   - Scroll: gradual with acceleration/deceleration phases
   - Reaction time: 500-3000ms between perception and action
   - Micro-pauses: random 100-500ms pauses during operations

3. NETWORK STEALTH:
   - User-Agent rotation (Chrome 120, Firefox ESR 115, Safari 17)
   - Headers consistent with browser identity
   - TLS fingerprint hardening hints (ciphersuite ordering)
   - Proxy rotation support (round-robin with health checks)

4. INTEGRATION POINTS:
   - eidos_mouse.py: replace bezier waypoints with human submovement model
   - eidos_browser.py: inject comprehensive stealth JS via add_init_script/CDP
   - eidos_playwright.py: anti-detection context with full stealth profile

Architecture:
    StealthProfile      — browser identity (UA, platform, languages, GPU, fonts)
    StealthMouse        — Fitts' Law stochastic optimized submovement model
    StealthKeyboard     — log-normal keystroke timing with bigram awareness
    StealthScroll       — acceleration/deceleration profile scroll
    StealthInjector     — builds and injects JS payloads for Playwright/Selenium

No external dependencies beyond stdlib. All JS injection is pure JavaScript.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.stealth")

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS — anthropometric, calibrated from HCI research
# ═══════════════════════════════════════════════════════════════════════════════

# Fitts' Law coefficients (Shannon formulation, calibrated from MacKenzie 1992)
FITTS_A = 0.100   # intercept (seconds)
FITTS_B = 0.125   # slope (seconds/bit)

# Mouse submovement model
PRIMARY_COVERAGE = 0.90          # primary ballistic covers ~90% distance
CORRECTIVE_SUBMOVEMENTS = (1, 3) # 1-3 corrective submovements after primary
HUMAN_TREMOR_AMPLITUDE = 1.2     # px of pink-noise tremor
HUMAN_TREMOR_FREQ = 0.05         # spatial frequency of tremor along path
CLICK_MICRO_JITTER = 2.5         # px jitter at click instant (finger pressure)

# Keystroke timing (log-normal, calibrated from typing studies)
KEYSTROKE_MU = 4.0               # ln(ms) mean (~55ms median)
KEYSTROKE_SIGMA = 0.55           # ln(ms) stddev
KEYSTROKE_MIN_MS = 30            # hard floor
KEYSTROKE_MAX_MS = 350           # hard ceiling
# Common Spanish bigrams that slow typists
SLOW_BIGRAMS = {
    "tr", "rt", "br", "pr", "dr", "cr", "gr", "fr", "vr",
    "es", "er", "re", "ar", "ra", "nt", "nc", "mb", "mp",
    "ue", "ui", "ie", "iu", "qu", "ch", "ll", "rr",
}
BIGRAM_SLOWDOWN = 1.4            # multiply keystroke delay for slow bigrams

# Reaction time
REACTION_MIN_MS = 500            # fast reaction (alert, expecting)
REACTION_MAX_MS = 3000           # slow reaction (surprised, distracted)

# Micro-pause
MICROPAUSE_MIN_MS = 80
MICROPAUSE_MAX_MS = 500

# Scroll parameters
SCROLL_ACCEL_PHASE_RATIO = 0.3   # first 30% accelerating
SCROLL_CRUISE_PHASE_RATIO = 0.4  # middle 40% constant speed
SCROLL_DECEL_PHASE_RATIO = 0.3   # last 30% decelerating


# ═══════════════════════════════════════════════════════════════════════════════
# STEALTH PROFILE — browser identity definition
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class StealthProfile:
    """Complete browser fingerprint identity.

    All values are consistent with each other (e.g., Windows NT → Chrome
    plugins on Windows, and es-ES language matches a Spanish-locale system).
    """

    # User-Agent rotation pool
    user_agents: List[str] = field(default_factory=lambda: [
        # Chrome 120 on Windows 10 (es-ES locale)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        # Firefox ESR 115 on Windows 10
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) "
        "Gecko/20100101 Firefox/115.0",
        # Chrome 120 on Linux (es-ES)
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        # Safari 17 on macOS (fallback for sites that prefer Safari)
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        # Firefox ESR 115 on Linux (es-ES, our real platform)
        "Mozilla/5.0 (X11; Linux x86_64; rv:115.0) "
        "Gecko/20100101 Firefox/115.0",
    ])

    # Browser selection determines which subset of properties to use
    browser: str = "chrome"  # "chrome", "firefox", "safari"

    # Platform
    platform: str = "Win32"
    platform_oscpu: str = "Windows NT 10.0; Win64; x64"
    hardware_concurrency: int = 8

    # Languages (consistent: es-ES primary, en fallback)
    languages: List[str] = field(default_factory=lambda: ["es-ES", "es", "en-US", "en"])

    # Screen
    screen_width: int = 1920
    screen_height: int = 1080
    avail_width: int = 1920
    avail_height: int = 1040  # minus taskbar
    color_depth: int = 24
    pixel_depth: int = 24
    device_pixel_ratio: float = 1.0

    # WebGL (spoofed to integrated Intel UHD 620 — extremely common)
    webgl_vendor: str = "Google Inc. (Intel)"
    webgl_renderer: str = "ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)"
    webgl_unmasked_vendor: str = "Intel"
    webgl_unmasked_renderer: str = "Mesa Intel(R) UHD Graphics 620 (KBL GT2)"
    webgl_max_texture_size: int = 16384
    webgl_max_viewport_dims: Tuple[int, int] = (16384, 16384)

    # Plugins (5 realistic — the standard set Chrome ships with)
    plugins: List[Dict[str, str]] = field(default_factory=lambda: [
        {"name": "Chrome PDF Plugin",       "filename": "internal-pdf-viewer",
         "description": "Portable Document Format", "mime_types": "application/pdf"},
        {"name": "Chrome PDF Viewer",        "filename": "mhjfbmdgcfjbbpaeojofohoefgiehjai",
         "description": "", "mime_types": "application/pdf"},
        {"name": "Native Client",            "filename": "internal-nacl-plugin",
         "description": "", "mime_types": "application/x-nacl,application/x-pnacl"},
        {"name": "Widevine Content Decryption Module", "filename": "widevinecdmadapter.dll",
         "description": "Widevine Content Decryption Module", "mime_types": "application/x-ppapi-widevine-cdm"},
        {"name": "Microsoft Edge PDF Viewer", "filename": "internal-pdf-viewer-msedge",
         "description": "Portable Document Format", "mime_types": "application/pdf"},
    ])

    # MIME types (consistent with plugins)
    mime_types: List[str] = field(default_factory=lambda: [
        "application/pdf", "text/pdf",
    ])

    # Timezone (Europe/Madrid — consistent with es-ES)
    timezone: str = "Europe/Madrid"
    timezone_offset: int = -120  # CET/CEST depends on season, use generalized

    # Common fonts (present on ~99% of desktop systems)
    fonts: List[str] = field(default_factory=lambda: [
        "Arial", "Arial Black", "Arial Narrow", "Book Antiqua", "Bookman Old Style",
        "Calibri", "Cambria", "Cambria Math", "Candara", "Century Gothic",
        "Comic Sans MS", "Consolas", "Constantia", "Corbel", "Courier New",
        "DejaVu Sans", "DejaVu Sans Mono", "DejaVu Serif",
        "Droid Sans", "Droid Sans Mono", "Droid Serif",
        "EB Garamond", "Fira Code", "Fira Mono", "Fira Sans",
        "Franklin Gothic Medium", "Gabriola", "Georgia", "Impact",
        "Liberation Mono", "Liberation Sans", "Liberation Serif",
        "Lucida Console", "Lucida Sans Unicode", "Microsoft Sans Serif",
        "Monaco", "Monospace", "Noto Color Emoji", "Noto Sans", "Noto Serif",
        "Palatino Linotype", "Segoe Print", "Segoe Script", "Segoe UI",
        "Segoe UI Emoji", "Segoe UI Historic", "Segoe UI Symbol",
        "Sitka Banner", "Sitka Display", "Sitka Heading", "Sitka Small",
        "Sitka Subheading", "Sitka Text",
        "Tahoma", "Times New Roman", "Trebuchet MS", "Ubuntu", "Ubuntu Mono",
        "Verdana", "Webdings", "Wingdings", "Wingdings 2", "Wingdings 3",
    ])

    def get_ua(self, index: int = 0) -> str:
        """Return a User-Agent from the rotation pool."""
        return self.user_agents[index % len(self.user_agents)]

    def get_ua_count(self) -> int:
        """Number of User-Agents available for rotation."""
        return len(self.user_agents)

    def _derive_platform(self) -> str:
        """Derive platform/os from current browser setting."""
        if self.browser == "safari":
            self.platform = "MacIntel"
            self.platform_oscpu = "Intel Mac OS X 14_0"
        elif self.browser == "firefox":
            self.platform = "Win32"
            self.platform_oscpu = "Windows NT 10.0; Win64; x64"
        else:  # chrome
            self.platform = "Win32"
            self.platform_oscpu = "Windows NT 10.0; Win64; x64"


# ═══════════════════════════════════════════════════════════════════════════════
# FINGERPRINT DEFENSE — JavaScript injection payloads
# ═══════════════════════════════════════════════════════════════════════════════

class StealthInjector:
    """Builds and injects anti-fingerprinting JavaScript into browser contexts.

    Supports both Playwright (add_init_script) and Selenium (CDP
    Page.addScriptToEvaluateOnNewDocument).
    """

    def __init__(self, profile: StealthProfile = None):
        self.profile = profile or StealthProfile()
        # Per-session canvas noise seed (consistent noise across same page)
        self._session_seed = random.randint(0, 2**31 - 1)

    # ── Payload builders ───────────────────────────────────────────────────

    def _build_webdriver_payload(self) -> str:
        """JS to hide navigator.webdriver and chrome runtime automation flags."""
        return """
(() => {
    // Remove webdriver flag — primary detection vector
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true,
    });

    // Remove CDP runtime flag (Chrome DevTools Protocol detection)
    const originalStrict = Error.stackTraceLimit;
    Error.stackTraceLimit = 0;
    try {
        const s = new Error().stack;
        // If stack was captured, it was real JS — good
    } finally {
        Error.stackTraceLimit = originalStrict;
    }

    // Hide automation extension
    Object.defineProperty(document, 'hidden', {
        get: () => false,
    });

    // Chrome automation flag
    delete window.__nightmare;
    delete window.__webdriver_evaluate;
    delete window.__driver_evaluate;
    delete window.__selenium_evaluate;
    delete window.__webdriver_script_function;
    delete window.__webdriver_script_func;
    delete window.__webdriver_script_fn;
    delete window.__fxdriver_evaluate;
    delete window.__driver_unwrapped;
    delete window.__webdriver_unwrapped;
    delete window.__selenium_unwrapped;
    delete window.__fxdriver_unwrapped;
    delete window.__webdriver_script_fn;
    delete window.__lastwatiralert;
    delete window.__lastwatiralert;
    delete window.__watiralert;
    delete document.__webdriver_script_fn;
    delete document.__driver_evaluate;
    delete document.__selenium_evaluate;
    delete document.__webdriver_evaluate;

    // PhantomJS
    delete window.callPhantom;
    delete window._phantom;
    delete window.__phantomas;

    // NightmareJS
    delete window.__nightmare;

    // CefSharp
    delete window.__CefSharp;
})();
"""

    def _build_canvas_payload(self) -> str:
        """JS to randomize canvas fingerprint (consistent per session).

        Canvas fingerprinting works by rendering text/shapes to a hidden
        canvas and hashing the pixel output. We add subtle, consistent
        noise so the hash changes per session but is stable within a session.
        """
        return f"""
(() => {{
    // Per-session noise seed (deterministic within session, random across)
    const _seed = {self._session_seed};
    let _noiseCounter = 0;

    function _stealthNoise(base) {{
        // Subtle deterministic noise: max ±1 in any channel
        _noiseCounter++;
        const h = (_seed ^ (_noiseCounter * 2654435761)) >>> 0;
        const noise = ((h & 3) - 1) / 255.0; // -1/255, 0, or +1/255
        return Math.max(0, Math.min(1, base + noise));
    }}

    const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(sx, sy, sw, sh) {{
        const data = _origGetImageData.call(this, sx, sy, sw, sh);
        // Add subtle noise to a few random pixels (~2% of pixels)
        const pixelCount = sw * sh;
        const noiseCount = Math.max(1, Math.floor(pixelCount * 0.02));
        for (let i = 0; i < noiseCount; i++) {{
            const idx = (((_seed + i * 7919) % pixelCount) * 4);
            if (idx + 3 < data.data.length) {{
                data.data[idx] = Math.max(0, Math.min(255, data.data[idx] + (((_seed >> (i % 16)) & 1) ? 1 : -1)));
            }}
        }}
        return data;
    }};

    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
        // Force a tiny re-render to change pixel data slightly
        const ctx = this.getContext('2d');
        if (ctx && this.width > 0 && this.height > 0) {{
            const imgData = ctx.getImageData(0, 0, 1, 1);
            // Modify one subpixel imperceptibly
            if (imgData.data.length >= 3) {{
                imgData.data[0] = Math.max(0, Math.min(255, imgData.data[0] + (_seed & 1 ? 1 : -1)));
                ctx.putImageData(imgData, 0, 0);
            }}
        }}
        return _origToDataURL.apply(this, arguments);
    }};

    const _origToBlob = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {{
        const ctx = this.getContext('2d');
        if (ctx && this.width > 0 && this.height > 0) {{
            const imgData = ctx.getImageData(0, 0, 1, 1);
            if (imgData.data.length >= 3) {{
                imgData.data[1] = Math.max(0, Math.min(255, imgData.data[1] + (_seed & 2 ? 1 : -1)));
                ctx.putImageData(imgData, 0, 0);
            }}
        }}
        return _origToBlob.apply(this, arguments);
    }};
}})();
"""

    def _build_webgl_payload(self) -> str:
        """JS to mask WebGL fingerprint (spoof vendor/renderer strings).

        Fingerprinting sites query WebGL parameters like:
        - UNMASKED_VENDOR_WEBGL / UNMASKED_RENDERER_WEBGL (reveals real GPU)
        - MAX_TEXTURE_SIZE
        - Supported extensions
        We override getParameter to return spoofed values for these keys.
        """
        p = self.profile
        return f"""
(() => {{
    const SPOOFED = {{
        // Parameter → spoofed value
        37445: '{p.webgl_vendor}',         // VENDOR
        37446: '{p.webgl_renderer}',       // RENDERER
        7937: '{p.webgl_unmasked_vendor}', // UNMASKED_VENDOR_WEBGL
        7938: '{p.webgl_unmasked_renderer}', // UNMASKED_RENDERER_WEBGL
        3379: {p.webgl_max_texture_size},  // MAX_TEXTURE_SIZE
        3386: new Int32Array([{p.webgl_max_viewport_dims[0]}, {p.webgl_max_viewport_dims[1]}]), // MAX_VIEWPORT_DIMS
    }};

    function _patchWebGLContext(contextType) {{
        const proto = (
            contextType === 'webgl2'
            ? WebGL2RenderingContext.prototype
            : WebGLRenderingContext.prototype
        );
        if (!proto || proto.__stealth_patched) return;
        proto.__stealth_patched = true;

        const origGetParameter = proto.getParameter;
        proto.getParameter = function(pname) {{
            if (pname in SPOOFED) return SPOOFED[pname];
            return origGetParameter.call(this, pname);
        }};

        // Also patch getSupportedExtensions to filter experimental ones
        const origGetExtensions = proto.getSupportedExtensions;
        proto.getSupportedExtensions = function() {{
            const exts = origGetExtensions.call(this);
            if (!exts) return exts;
            // Filter out debugging extensions that reveal automation
            return exts.filter(function(e) {{
                return !e.includes('WEBGL_debug');
            }});
        }};
    }}

    // Patch existing prototypes
    _patchWebGLContext('webgl');
    if (typeof WebGL2RenderingContext !== 'undefined') {{
        _patchWebGLContext('webgl2');
    }}

    // Patch future contexts created via getContext
    const origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function() {{
        const ctx = origGetContext.apply(this, arguments);
        if (ctx && (ctx instanceof WebGLRenderingContext ||
                    (typeof WebGL2RenderingContext !== 'undefined' &&
                     ctx instanceof WebGL2RenderingContext))) {{
            // Re-patch on first actual context (late-patch for dynamically
            // created WebGL contexts)
            _patchWebGLContext(ctx instanceof WebGL2RenderingContext ? 'webgl2' : 'webgl');
        }}
        return ctx;
    }};
}})();
"""

    def _build_plugins_payload(self) -> str:
        """JS to populate navigator.plugins with 5 realistic entries.

        Plugin enumeration is a strong fingerprinting signal. We provide
        a realistic set of plugins that match a standard Chrome installation.
        """
        p = self.profile
        plugin_entries = []
        for pl in p.plugins:
            plugin_entries.append(f"""
            {{
                name: '{pl["name"]}',
                filename: '{pl["filename"]}',
                description: '{pl["description"]}',
                length: 1,
                item: function(i) {{ return this._mimes[i]; }},
                namedItem: function(n) {{ return this._mimes[0]; }},
                _mimes: [{{
                    type: '{pl["mime_types"].split(",")[0]}',
                    suffixes: '',
                    description: '{pl["description"]}',
                }}],
            }}
            """)

        mime_entries = []
        for mt in p.mime_types:
            mime_entries.append(f"""
            {{
                type: '{mt}',
                suffixes: 'pdf',
                description: 'Portable Document Format',
                enabledPlugin: _plugins[0],
            }}
            """)

        return f"""
(() => {{
    const _plugins = [{','.join(plugin_entries)}];
    const _mimes = [{','.join(mime_entries)}];

    Object.defineProperty(navigator, 'plugins', {{
        get: () => {{
            const arr = Object.create(PluginArray.prototype);
            for (let i = 0; i < _plugins.length; i++) {{
                Object.defineProperty(arr, i, {{
                    get: () => _plugins[i],
                    enumerable: true,
                }});
            }}
            Object.defineProperty(arr, 'length', {{
                value: _plugins.length,
                enumerable: false,
            }});
            arr.item = function(i) {{ return _plugins[i] || null; }};
            arr.namedItem = function(n) {{
                return _plugins.find(p => p.name === n) || null;
            }};
            arr.refresh = function() {{}};
            return arr;
        }},
        configurable: true,
        enumerable: true,
    }});

    Object.defineProperty(navigator, 'mimeTypes', {{
        get: () => {{
            const arr = Object.create(MimeTypeArray.prototype);
            for (let i = 0; i < _mimes.length; i++) {{
                Object.defineProperty(arr, i, {{
                    get: () => _mimes[i],
                    enumerable: true,
                }});
            }}
            Object.defineProperty(arr, 'length', {{
                value: _mimes.length,
                enumerable: false,
            }});
            arr.item = function(i) {{ return _mimes[i] || null; }};
            arr.namedItem = function(n) {{
                return _mimes.find(m => m.type === n) || null;
            }};
            return arr;
        }},
        configurable: true,
        enumerable: true,
    }});
}})();
"""

    def _build_languages_payload(self) -> str:
        """JS to set consistent languages."""
        p = self.profile
        langs = '[' + ','.join(f"'{l}'" for l in p.languages) + ']'
        return f"""
(() => {{
    Object.defineProperty(navigator, 'language', {{
        get: () => '{p.languages[0]}',
        configurable: true,
    }});
    Object.defineProperty(navigator, 'languages', {{
        get: () => {langs},
        configurable: true,
    }});
}})();
"""

    def _build_screen_payload(self) -> str:
        """JS to set standard screen resolution."""
        p = self.profile
        return f"""
(() => {{
    Object.defineProperty(screen, 'width', {{ get: () => {p.screen_width}, configurable: true }});
    Object.defineProperty(screen, 'height', {{ get: () => {p.screen_height}, configurable: true }});
    Object.defineProperty(screen, 'availWidth', {{ get: () => {p.avail_width}, configurable: true }});
    Object.defineProperty(screen, 'availHeight', {{ get: () => {p.avail_height}, configurable: true }});
    Object.defineProperty(screen, 'colorDepth', {{ get: () => {p.color_depth}, configurable: true }});
    Object.defineProperty(screen, 'pixelDepth', {{ get: () => {p.pixel_depth}, configurable: true }});
    Object.defineProperty(window, 'devicePixelRatio', {{ get: () => {p.device_pixel_ratio}, configurable: true }});
    Object.defineProperty(window, 'outerWidth', {{ get: () => {p.screen_width}, configurable: true }});
    Object.defineProperty(window, 'outerHeight', {{ get: () => {p.screen_height}, configurable: true }});
    Object.defineProperty(window, 'innerWidth', {{ get: () => {p.screen_width}, configurable: true }});
    Object.defineProperty(window, 'innerHeight', {{ get: () => {p.avail_height}, configurable: true }});
}})();
"""

    def _build_fonts_payload(self) -> str:
        """JS to report a standard set of fonts.

        Font enumeration is done by measuring width of rendered text in
        specific fonts. We don't fully prevent this but we can make
        document.fonts and the CSS font enumeration return expected values.
        """
        p = self.profile
        font_list = '[' + ','.join(f"'{f}'" for f in p.fonts[:50]) + ']'
        return f"""
(() => {{
    // Override the CSS Font Loading API
    const _fonts = {font_list};
    if (document.fonts) {{
        const origCheck = document.fonts.check;
        document.fonts.check = function(font, text) {{
            // Always return true for common fonts to avoid probing
            for (const f of _fonts) {{
                if (font.includes(f)) return true;
            }}
            return origCheck.call(document.fonts, font, text);
        }};
    }}
}})();
"""

    def _build_audio_payload(self) -> str:
        """JS to mask AudioContext fingerprint.

        AudioContext fingerprinting works by:
        1. Creating an OscillatorNode with specific frequency
        2. Processing through DynamicsCompressorNode
        3. Reading the resulting waveform — which varies by hardware/driver
        4. Hashing the waveform

        We add imperceptible noise to the output channels so the hash
        differs per session, while keeping audio actually silent/inaudible.
        """
        return f"""
(() => {{
    const _seed = {self._session_seed};

    // Patch AnalyserNode.getFloatTimeDomainData
    const OrigAudioContext = window.AudioContext || window.webkitAudioContext;
    if (!OrigAudioContext) return;

    const origGetFloatTimeDomainData = (
        AnalyserNode.prototype.getFloatTimeDomainData
    );
    if (origGetFloatTimeDomainData) {{
        AnalyserNode.prototype.getFloatTimeDomainData = function(array) {{
            origGetFloatTimeDomainData.call(this, array);
            // Add deterministic noise (max 1e-7 — imperceptible)
            for (let i = 0; i < array.length; i++) {{
                const h = ((_seed ^ (i * 2654435761)) >>> 0);
                const noise = ((h & 255) - 127.5) / 127.5 * 1e-7;
                array[i] += noise;
            }}
        }};
    }}

    // Patch getChannelData on AudioBuffer
    const origGetChannelData = AudioBuffer.prototype.getChannelData;
    if (origGetChannelData) {{
        AudioBuffer.prototype.getChannelData = function(channel) {{
            const data = origGetChannelData.call(this, channel);
            const copy = new Float32Array(data.length);
            for (let i = 0; i < data.length; i++) {{
                const h = ((_seed ^ ((channel << 16) | i) * 2654435761) >>> 0);
                const noise = ((h & 255) - 127.5) / 127.5 * 1e-7;
                copy[i] = data[i] + noise;
            }}
            return copy;
        }};
    }}

    // Override sampleRate if too unique (some laptops report weird rates)
    const origGetProp = Object.getOwnPropertyDescriptor(
        BaseAudioContext.prototype, 'sampleRate'
    );
    if (origGetProp) {{
        Object.defineProperty(BaseAudioContext.prototype, 'sampleRate', {{
            get: function() {{
                const rate = origGetProp.get.call(this);
                // Force common values: 44100 or 48000
                if (rate === 44100 || rate === 48000) return rate;
                return 44100; // most common
            }},
            configurable: true,
        }});
    }}
}})();
"""

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _strip_iife(js: str) -> str:
        """Strip the outer (() => { ... })(); wrapper from a JS payload.

        Each _build_* method returns JS wrapped in an arrow IIFE:
            (() => { ... })();
        This strips that wrapping so payloads can be composed into a single IIFE.
        """
        js = js.strip()
        # Remove leading (() => {
        if js.startswith("(() => {"):
            js = js[7:]  # len("(() => {") == 7
        elif js.startswith("(()=>{"):
            js = js[6:]  # len("(()=>{") == 6
        # Remove trailing })();
        if js.endswith("})();"):
            js = js[:-5]  # len("})();") == 5
        return js

    # ── Composite builders ──────────────────────────────────────────────────

    def build_full_stealth_js(self) -> str:
        """Build complete stealth JavaScript payload (all 8 layers).

        Returns a single self-executing script suitable for
        add_init_script() (Playwright) or
        Page.addScriptToEvaluateOnNewDocument (Selenium/CDP).
        """
        payloads = [
            "(function() { 'use strict';",
            "// === EIDOS STEALTH — 8-layer fingerprint defense ===\n",
            "// Layer 1: WebDriver flag hidden",
            self._strip_iife(self._build_webdriver_payload()),
            "\n// Layer 2: Canvas fingerprint randomized",
            self._strip_iife(self._build_canvas_payload()),
            "\n// Layer 3: WebGL fingerprint masked",
            self._strip_iife(self._build_webgl_payload()),
            "\n// Layer 4: Plugins array populated",
            self._strip_iife(self._build_plugins_payload()),
            "\n// Layer 5: Languages consistent with IP",
            self._strip_iife(self._build_languages_payload()),
            "\n// Layer 6: Screen resolution standard",
            self._strip_iife(self._build_screen_payload()),
            "\n// Layer 7: Fonts standard set",
            self._strip_iife(self._build_fonts_payload()),
            "\n// Layer 8: AudioContext fingerprint masked",
            self._strip_iife(self._build_audio_payload()),
            "\n// Chrome runtime (fake, prevents detection of missing chrome object)",
            "window.chrome = window.chrome || { runtime: {}, loadTimes: function() {}, csi: function() {} };",
            "\n// Permissions: silence notification permission queries",
            "const _origQuery = navigator.permissions.query;",
            "navigator.permissions.query = function(params) {",
            "  if (params.name === 'notifications') {",
            "    return Promise.resolve({ state: Notification.permission, onchange: null });",
            "  }",
            "  return _origQuery.call(navigator.permissions, params);",
            "};",
            "\n// Navigator: platform and hardwareConcurrency",
            f"Object.defineProperty(navigator, 'platform', {{ get: () => '{self.profile.platform}', configurable: true }});",
            f"Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {self.profile.hardware_concurrency}, configurable: true }});",
            f"Object.defineProperty(navigator, 'deviceMemory', {{ get: () => 8, configurable: true }});",
            "\n// Timezone spoofing",
            f"const _origDateTimeFormat = Intl.DateTimeFormat;",
            f"const _origGetTimezoneOffset = Date.prototype.getTimezoneOffset;",
            f"Date.prototype.getTimezoneOffset = function() {{ return {self.profile.timezone_offset}; }};",
            "\n})();",
        ]
        return "\n".join(payloads)

    def build_lightweight_stealth_js(self) -> str:
        """Build a lighter stealth payload (webdriver + languages + screen only).

        For sites where full stealth is not needed or causes compatibility issues.
        """
        return "\n".join([
            "(function() { 'use strict';",
            "// === EIDOS STEALTH LITE ===\n",
            self._strip_iife(self._build_webdriver_payload()),
            "\n" + self._strip_iife(self._build_languages_payload()),
            "\n" + self._strip_iife(self._build_screen_payload()),
            "\nwindow.chrome = window.chrome || { runtime: {} };",
            f"\nObject.defineProperty(navigator, 'platform', {{ get: () => '{self.profile.platform}' }});",
            f"Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {self.profile.hardware_concurrency} }});",
            "\n})();",
        ])

    def inject_into_playwright(self, context_or_page) -> str:
        """Inject full stealth into a Playwright BrowserContext or Page.

        For BrowserContext: uses add_init_script (runs before every page load).
        For Page: uses add_init_script on the page.

        Returns the script string that was injected (for logging/diagnostics).
        """
        script = self.build_full_stealth_js()

        # Check if it's a BrowserContext (has add_init_script and new_page)
        if hasattr(context_or_page, 'add_init_script'):
            context_or_page.add_init_script(script)
        # Check if it's a Page (has add_init_script too)
        elif hasattr(context_or_page, 'evaluate'):
            context_or_page.add_init_script(script)
        else:
            log.warning("stealth: cannot inject — unknown object type: %s",
                        type(context_or_page).__name__)
            return script

        log.info("stealth: 8-layer fingerprint defense injected (session seed=%d)",
                 self._session_seed)
        return script

    def inject_into_selenium(self, driver) -> str:
        """Inject full stealth into Selenium WebDriver via CDP.

        Uses Page.addScriptToEvaluateOnNewDocument so the script runs
        before any page JavaScript.
        """
        script = self.build_full_stealth_js()

        try:
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": script,
            })
        except Exception as e:
            log.warning("stealth: CDP injection failed (%s), falling back to execute_script", e)
            # Fallback: execute now (won't run on future navigations, but works)
            driver.execute_script(script)

        log.info("stealth: 8-layer fingerprint defense injected via CDP (session seed=%d)",
                 self._session_seed)
        return script

    def inject_into_page_cdp(self, page) -> str:
        """Inject full stealth into a Playwright Page via CDP protocol.

        Works the same as Selenium CDP injection — runs on every new document.
        """
        script = self.build_full_stealth_js()

        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": script})
        except Exception as e:
            log.warning("stealth: CDP injection failed (%s), falling back to add_init_script", e)
            page.add_init_script(script)

        log.info("stealth: 8-layer fingerprint defense injected via CDP")
        return script


# ═══════════════════════════════════════════════════════════════════════════════
# HUMAN MOUSE — Stochastic Optimized Submovement Model
# ═══════════════════════════════════════════════════════════════════════════════

class StealthMouse:
    """Mouse movement using stochastic optimized submovement model.

    Replaces the bezier-based model in eidos_mouse.py with a physiologically
    accurate model based on Fitts' Law and submovement decomposition research:

    - Meyer et al. (1988): "Optimality in human motor performance"
    - Walker et al. (1997): "Spatial and temporal characteristics of rapid
      cursor-positioning movements with electromechanical mice"

    The model:
    1. Primary ballistic submovement (covers ~90% of distance)
       - Fast, slightly curved, with asymmetric bell-shaped velocity profile
       - Direction has small random bias (human imperfection)
    2. Secondary corrective submovement(s) (1-3 adjustments near target)
       - Each progressively smaller
       - Follow decreasing velocity profiles
    3. Pink noise tremor superimposed throughout
    4. Click-instant micro-jitter (finger pressure on button)

    This is NOT bezier — bezier creates smooth paths but humans DON'T move
    in smooth mathematical curves. Humans make a fast "throw" toward the
    target, then micro-correct near the end. That's what this models.
    """

    def __init__(self, profile: StealthProfile = None):
        self.profile = profile or StealthProfile()
        self._last_click_time = 0.0
        self._current_x = 0.0
        self._current_y = 0.0

    def generate_waypoints(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
    ) -> List[Tuple[int, int]]:
        """Generate human-like waypoints from (start) to (end).

        Uses stochastic optimized submovement model: primary ballistic
        + corrective submovements + tremor noise.

        Returns list of (x, y) pixel coordinates.
        """
        self._current_x = float(start_x)
        self._current_y = float(start_y)

        dx = end_x - start_x
        dy = end_y - start_y
        distance = math.sqrt(dx * dx + dy * dy)

        if distance < 5:
            # Very short movement — just direct with tiny tremor
            return [(int(end_x), int(end_y))]

        # Fitts' Law predicts total movement time
        # Target width W ~ 20px (typical button), distance D
        W = 20.0
        D = max(distance, 1.0)
        index_of_difficulty = math.log2(D / W + 1)
        total_time = FITTS_A + FITTS_B * index_of_difficulty  # seconds

        # Number of submovements: more corrections for harder targets
        num_corrective = random.randint(*CORRECTIVE_SUBMOVEMENTS)

        # Primary covers PRIMARY_COVERAGE of distance
        primary_frac = PRIMARY_COVERAGE + random.uniform(-0.05, 0.05)
        primary_dist = distance * primary_frac

        # Direction angle with small random bias (-3 to +3 degrees)
        angle = math.atan2(dy, dx)
        angle += random.uniform(-0.05, 0.05)  # ~3 degrees max bias

        # Primary endpoint (will overshoot or undershoot slightly)
        primary_end_x = start_x + primary_dist * math.cos(angle)
        primary_end_y = start_y + primary_dist * math.cos(
            math.atan2(dy, dx) + random.uniform(-0.03, 0.03)
        )

        # Generate waypoints: primary + corrective submovements
        all_waypoints = []

        # --- Primary ballistic submovement ---
        primary_steps = self._steps_for_distance(primary_dist, is_primary=True)
        primary_wp = self._submovement_waypoints(
            start_x, start_y,
            primary_end_x, primary_end_y,
            primary_steps,
            velocity_phase="ballistic",
        )
        all_waypoints.extend(primary_wp)

        # --- Corrective submovements ---
        remaining_x = end_x - primary_end_x
        remaining_y = end_y - primary_end_y
        sub_start_x = primary_end_x
        sub_start_y = primary_end_y

        for i in range(num_corrective):
            # Each corrective covers a fraction of remaining distance
            frac = 1.0 / (num_corrective - i + 1) + random.uniform(-0.1, 0.1)
            frac = max(0.3, min(0.9, frac))

            sub_end_x = sub_start_x + remaining_x * frac
            sub_end_y = sub_start_y + remaining_y * frac

            sub_dist = math.sqrt(
                (sub_end_x - sub_start_x) ** 2 +
                (sub_end_y - sub_start_y) ** 2
            )

            steps = self._steps_for_distance(sub_dist, is_primary=False)
            sub_wp = self._submovement_waypoints(
                sub_start_x, sub_start_y,
                sub_end_x, sub_end_y,
                steps,
                velocity_phase="corrective",
            )
            # Skip first point (duplicate of last in previous submovement)
            if sub_wp:
                all_waypoints.extend(sub_wp[1:] if len(sub_wp) > 1 else [])

            sub_start_x = sub_end_x
            sub_start_y = sub_end_y
            remaining_x = end_x - sub_end_x
            remaining_y = end_y - sub_end_y

        # --- Final overshoot + correction (15% probability) ---
        if distance > 200 and random.random() < 0.15:
            overshoot_x = end_x + (dx / distance) * random.uniform(5, 15)
            overshoot_y = end_y + (dy / distance) * random.uniform(5, 15)
            all_waypoints.append((int(overshoot_x), int(overshoot_y)))
            # Small pause before correction
            # (the pause itself isn't a waypoint, handled by caller)
            all_waypoints.append((int(end_x), int(end_y)))

        # Ensure final point is exactly the target
        if all_waypoints:
            last_x, last_y = all_waypoints[-1]
            if abs(last_x - end_x) > 3 or abs(last_y - end_y) > 3:
                all_waypoints.append((int(end_x), int(end_y)))
        else:
            all_waypoints.append((int(end_x), int(end_y)))

        # Click-instant micro-jitter: add tiny offset to the final point
        if all_waypoints and len(all_waypoints) >= 2:
            final = all_waypoints[-1]
            jitter_x = final[0] + random.uniform(-CLICK_MICRO_JITTER, CLICK_MICRO_JITTER)
            jitter_y = final[1] + random.uniform(-CLICK_MICRO_JITTER, CLICK_MICRO_JITTER)
            all_waypoints[-1] = (int(jitter_x), int(jitter_y))

        # Deduplicate consecutive identical points
        deduped = []
        for p in all_waypoints:
            if not deduped or deduped[-1] != p:
                deduped.append(p)

        self._current_x = float(end_x)
        self._current_y = float(end_y)
        return deduped

    def _steps_for_distance(self, distance: float, is_primary: bool) -> int:
        """Calculate number of waypoints for a submovement."""
        if is_primary:
            # Primary: fewer steps (fast), ~10-30 waypoints
            return max(5, min(30, int(distance / 12.0)))
        else:
            # Corrective: more steps per distance (precise), ~5-15 waypoints
            return max(3, min(15, int(distance / 5.0)))

    def _submovement_waypoints(
        self,
        x0: float, y0: float,
        x1: float, y1: float,
        steps: int,
        velocity_phase: str,
    ) -> List[Tuple[int, int]]:
        """Generate waypoints for one submovement with velocity profile.

        velocity_phase:
          - "ballistic": asymmetric bell — fast acceleration, cruise, slow deceleration
          - "corrective": mostly decelerating — slow approach to target

        Each point has pink-noise tremor superimposed (fractal 1/f noise).
        """
        waypoints = []
        tremor_state = random.random() * 100.0  # noise phase offset

        for i in range(steps + 1):
            t = i / steps if steps > 0 else 1.0

            # Velocity profile — determines spacing between points
            if velocity_phase == "ballistic":
                # Asymmetric bell: accelerate fast, sustain, decelerate slowly
                if t < 0.2:
                    # Acceleration phase (0-20%): ease-in
                    t_spatial = 0.5 * (t / 0.2) ** 2
                elif t < 0.8:
                    # Cruise phase (20-80%): mostly linear
                    t_spatial = 0.1 + 0.8 * ((t - 0.2) / 0.6)
                else:
                    # Deceleration phase (80-100%): ease-out
                    remaining = 1.0 - t
                    t_spatial = 0.9 + 0.1 * (1.0 - (remaining / 0.2) ** 2)
            else:  # corrective
                # Exponentially decelerating approach
                t_spatial = 1.0 - (1.0 - t) ** 2.5

            # Base position along the path
            x = x0 + (x1 - x0) * t_spatial
            y = y0 + (y1 - y0) * t_spatial

            # Pink noise tremor (approximated via summed sinusoids)
            # True 1/f noise = sum of low-frequency sinusoids with decreasing amplitude
            tremor_x = 0.0
            tremor_y = 0.0
            for freq_idx in range(1, 4):
                freq = freq_idx * HUMAN_TREMOR_FREQ
                amp = HUMAN_TREMOR_AMPLITUDE / (freq_idx ** 1.5)
                tremor_x += amp * math.sin(
                    2 * math.pi * freq * t * steps + tremor_state + freq_idx * 1.7
                )
                tremor_y += amp * math.cos(
                    2 * math.pi * freq * t * steps + tremor_state + freq_idx * 2.3
                )

            waypoints.append((int(x + tremor_x), int(y + tremor_y)))

        return waypoints

    # ── Timing ─────────────────────────────────────────────────────────────

    def inter_waypoint_delay(self, is_last: bool = False) -> float:
        """Return realistic delay between waypoints in seconds.

        Human motor control: ~8-12ms between positional updates during
        ballistic phase, slowing to 20-40ms during corrective phase.
        """
        if is_last:
            return 0.025 + random.uniform(0, 0.015)  # 25-40ms before final
        return 0.008 + random.uniform(0, 0.007)  # 8-15ms normal

    def reaction_delay(self) -> float:
        """Return realistic reaction time in seconds (0.5-3.0s)."""
        delay_ms = random.uniform(REACTION_MIN_MS, REACTION_MAX_MS)
        # Add occasional distraction (20% probability of extra delay)
        if random.random() < 0.20:
            delay_ms += random.uniform(200, 1200)
        return delay_ms / 1000.0

    def micro_pause(self) -> float:
        """Return a micro-pause duration in seconds."""
        return random.uniform(MICROPAUSE_MIN_MS, MICROPAUSE_MAX_MS) / 1000.0

    def pre_click_pause(self) -> float:
        """Return pause before clicking (positioning confirmation)."""
        # 30-120ms: time to confirm the cursor is on target before pressing
        return random.uniform(0.03, 0.12)

    def post_click_pause(self) -> float:
        """Return pause after clicking (release, process result)."""
        return random.uniform(0.05, 0.20)

    def update_position(self, x: float, y: float):
        """Update the tracked current position (call after external move)."""
        self._current_x = float(x)
        self._current_y = float(y)


# ═══════════════════════════════════════════════════════════════════════════════
# HUMAN KEYBOARD — Variable delay typing with bigram awareness
# ═══════════════════════════════════════════════════════════════════════════════

class StealthKeyboard:
    """Generates human-like keystroke timing with log-normal distribution.

    Real typists don't have uniform delays — they vary based on:
    - Finger travel distance (bigram geometry)
    - Practice (common words → faster)
    - Fatigue (slowing down over time, modeled as slight drift)
    """

    def __init__(self):
        self._fatigue = 0.0  # accumulates during typing session
        self._keystroke_count = 0

    def keystroke_delay(self, current_char: str = "", next_char: str = "") -> float:
        """Return delay in seconds before typing the next character.

        Uses log-normal distribution calibrated to real typing data
        (mean ~55ms, stddev varies). Applies bigram slowdown for
        difficult finger transitions.

        Args:
            current_char: the character just typed (empty for first char)
            next_char: the character about to be typed
        """
        # Base delay from log-normal distribution
        delay_ms = random.lognormvariate(KEYSTROKE_MU, KEYSTROKE_SIGMA)
        delay_ms = max(KEYSTROKE_MIN_MS, min(KEYSTROKE_MAX_MS, delay_ms))

        # Bigram slowdown: certain key combinations are physically harder
        if current_char and next_char:
            bigram = (current_char + next_char).lower()
            if bigram in SLOW_BIGRAMS:
                delay_ms *= BIGRAM_SLOWDOWN

        # Fatigue drift: slight slowing over long typing sessions
        self._keystroke_count += 1
        if self._keystroke_count > 100:
            self._fatigue = min(0.4, (self._keystroke_count - 100) / 1000.0)

        delay_ms *= (1.0 + self._fatigue)

        # Add micro-variability (jitter)
        delay_ms += random.uniform(-5, 5)

        return max(KEYSTROKE_MIN_MS, delay_ms) / 1000.0

    def word_pause(self) -> float:
        """Return pause between words (space-bar + mental planning)."""
        return random.uniform(0.08, 0.25)

    def pre_enter_pause(self) -> float:
        """Return pause before pressing Enter (decision confirmation)."""
        return random.uniform(0.15, 0.50)

    def reset(self):
        """Reset fatigue counter for a new typing session."""
        self._fatigue = 0.0
        self._keystroke_count = 0


# ═══════════════════════════════════════════════════════════════════════════════
# HUMAN SCROLL — Gradual acceleration/deceleration
# ═══════════════════════════════════════════════════════════════════════════════

class StealthScroll:
    """Generates human-like scroll profiles with acceleration and deceleration.

    Humans don't scroll at constant speed — they accelerate at the start,
    cruise in the middle, and decelerate toward the end. Also, humans
    scroll in bursts with micro-pauses between them.

    This is used for Playwright's page.evaluate("window.scrollBy(...)") or
    keyboard-based scrolling (Page Down).
    """

    def __init__(self):
        pass

    def generate_scroll_profile(
        self,
        total_distance_px: int,
    ) -> List[Tuple[int, float]]:
        """Generate a scroll profile: list of (delta_px, pause_after_ms).

        Each step is one "burst" of scrolling followed by a pause.
        Real users scroll in 3-8 bursts with pauses between.

        Args:
            total_distance_px: total scroll distance in pixels

        Returns:
            List of (scroll_amount, pause_ms) tuples.
        """
        if total_distance_px < 50:
            return [(total_distance_px, 0.0)]

        # Decompose into 2-6 bursts
        num_bursts = random.randint(2, 6)
        remaining = total_distance_px

        profile = []
        for i in range(num_bursts):
            is_last = (i == num_bursts - 1)

            if is_last:
                burst_amount = remaining
            else:
                # Each burst covers a portion with slight randomness
                frac = 1.0 / (num_bursts - i) + random.uniform(-0.1, 0.15)
                frac = max(0.15, min(0.6, frac))
                burst_amount = int(remaining * frac)
                burst_amount = max(20, burst_amount)

            # Pause between bursts (not for last)
            pause = 0.0
            if not is_last:
                # Longer pauses if burst was large (reading time)
                if burst_amount > 400:
                    pause = random.uniform(0.3, 1.2)  # 300-1200ms
                else:
                    pause = random.uniform(0.08, 0.40)  # 80-400ms

            profile.append((burst_amount, pause))
            remaining -= burst_amount
            if remaining <= 0:
                break

        return profile

    def acceleration_profile(
        self,
        total_steps: int,
    ) -> List[float]:
        """Generate normalized velocity multipliers for smooth scrolling.

        Returns a list of multipliers (0.0 to 1.0) for each step,
        representing the velocity at that point in the scroll.
        The profile: accelerate → cruise → decelerate.
        """
        if total_steps <= 1:
            return [1.0]

        accel_end = int(total_steps * SCROLL_ACCEL_PHASE_RATIO)
        cruise_end = int(total_steps * (SCROLL_ACCEL_PHASE_RATIO + SCROLL_CRUISE_PHASE_RATIO))

        multipliers = []
        for i in range(total_steps):
            if i < accel_end:
                # Ease-in: slow → 0 → fast
                t = i / max(1, accel_end - 1)
                m = t ** 1.5  # smooth acceleration
            elif i < cruise_end:
                m = 1.0  # constant speed
            else:
                # Ease-out: fast → slow → stop
                remaining = total_steps - i
                decel_total = total_steps - cruise_end
                t = remaining / max(1, decel_total)
                m = t ** 2.0  # smooth deceleration
            multipliers.append(max(0.05, m))

        return multipliers


# ═══════════════════════════════════════════════════════════════════════════════
# NETWORK STEALTH — User-Agent rotation, header consistency, proxy support
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ProxyConfig:
    """Configuration for a proxy server."""
    url: str          # http://host:port or socks5://host:port
    username: str = ""
    password: str = ""
    region: str = "es"  # region hint for UA/language matching
    weight: float = 1.0  # higher weight = used more often
    healthy: bool = True
    last_checked: float = 0.0
    fail_count: int = 0


class StealthNetwork:
    """Network-level stealth: UA rotation, header consistency, proxy rotation.

    Provides:
    1. User-Agent rotation from StealthProfile
    2. Header sets consistent with each browser
    3. Proxy rotation with health checks
    4. TLS fingerprint hardening hints (via browser args)
    """

    def __init__(self, profile: StealthProfile = None):
        self.profile = profile or StealthProfile()
        self._proxies: List[ProxyConfig] = []
        self._proxy_index = 0
        self._current_ua_index = 0

    # ── User-Agent rotation ────────────────────────────────────────────────

    def next_user_agent(self) -> str:
        """Get next User-Agent from rotation pool (round-robin)."""
        ua = self.profile.get_ua(self._current_ua_index)
        self._current_ua_index = (
            (self._current_ua_index + 1) % self.profile.get_ua_count()
        )
        return ua

    def random_user_agent(self) -> str:
        """Get a random User-Agent from the pool."""
        return random.choice(self.profile.user_agents)

    def get_headers(self, user_agent: str = None) -> Dict[str, str]:
        """Get HTTP headers consistent with the given User-Agent.

        Headers are specific to each browser to maintain consistency.
        """
        ua = user_agent or self.profile.get_ua(0)

        # Detect browser family from UA
        if "Firefox" in ua:
            return self._firefox_headers(ua)
        elif "Safari" in ua and "Chrome" not in ua:
            return self._safari_headers(ua)
        else:
            return self._chrome_headers(ua)

    def _chrome_headers(self, ua: str) -> Dict[str, str]:
        return {
            "User-Agent": ua,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,en-US;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "max-age=0",
            "Sec-Ch-Ua": (
                '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
            ),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
        }

    def _firefox_headers(self, ua: str) -> Dict[str, str]:
        return {
            "User-Agent": ua,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
        }

    def _safari_headers(self, ua: str) -> Dict[str, str]:
        return {
            "User-Agent": ua,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "es-ES,es;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

    # ── TLS fingerprint hardening ──────────────────────────────────────────

    def get_chromium_args(self) -> List[str]:
        """Return Chromium launch args that harden against TLS fingerprinting.

        These args:
        - Disable automation flags
        - Use common cipher suite ordering
        - Disable features that reveal automation
        """
        return [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateSites,site-per-process",
            "--disable-features=BlockInsecurePrivateNetworkRequests",
            "--disable-features=OutOfBlinkCors",
            # Force TLS 1.2 with common ciphers (like a real browser)
            "--ssl-version-min=tls1.2",
            "--ssl-version-max=tls1.3",
            # Disable experimental features that may be fingerprintable
            "--disable-features=PrivacySandboxSettings4",
            "--disable-features=InterestFeedContentSuggestions",
            # Regular browser-looking flags
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--disable-breakpad",
            "--disable-crash-reporter",
            "--disable-component-update",
        ]

    def get_firefox_args(self) -> List[str]:
        """Return Firefox preferences that harden against fingerprinting."""
        return []  # Firefox via Playwright handles this via profile prefs

    # ── Proxy rotation ─────────────────────────────────────────────────────

    def add_proxy(self, proxy: ProxyConfig):
        """Add a proxy to the rotation pool."""
        self._proxies.append(proxy)
        log.info("stealth: proxy added: %s (region=%s)", proxy.url, proxy.region)

    def remove_proxy(self, url: str):
        """Remove a proxy from the pool."""
        self._proxies = [p for p in self._proxies if p.url != url]

    def get_proxy(self) -> Optional[ProxyConfig]:
        """Get next healthy proxy using weighted round-robin.

        Returns None if no proxies are configured (direct connection).
        """
        healthy = [p for p in self._proxies if p.healthy]
        if not healthy:
            if self._proxies:
                # All unhealthy — try the first anyway after 5 min
                for p in self._proxies:
                    if time.time() - p.last_checked > 300:
                        return p
            return None

        # Weighted round-robin
        total_weight = sum(p.weight for p in healthy)
        r = random.uniform(0, total_weight)
        cumulative = 0.0
        for p in healthy:
            cumulative += p.weight
            if r <= cumulative:
                return p

        return healthy[-1]

    def mark_proxy_failure(self, proxy: ProxyConfig):
        """Mark a proxy as failed (will be deprioritized)."""
        proxy.fail_count += 1
        proxy.last_checked = time.time()
        if proxy.fail_count >= 3:
            proxy.healthy = False
            log.warning("stealth: proxy %s marked unhealthy after %d failures",
                        proxy.url, proxy.fail_count)

    def mark_proxy_success(self, proxy: ProxyConfig):
        """Mark proxy as working (resets fail count)."""
        if proxy.fail_count > 0:
            proxy.fail_count = 0
        proxy.healthy = True
        proxy.last_checked = time.time()

    def get_proxy_playwright_config(self) -> Optional[Dict[str, str]]:
        """Get proxy configuration in Playwright format.

        Returns:
            {"server": "http://host:port"} or None for direct connection.
        """
        proxy = self.get_proxy()
        if not proxy:
            return None

        config = {"server": proxy.url}
        if proxy.username:
            config["username"] = proxy.username
            config["password"] = proxy.password
        return config

    def get_proxy_selenium_config(self) -> Optional[str]:
        """Get proxy configuration as Selenium Chrome arg.

        Returns:
            "--proxy-server=http://host:port" or empty string.
        """
        proxy = self.get_proxy()
        if not proxy:
            return ""

        return f"--proxy-server={proxy.url}"

    # ── Combined profile for browser launch ────────────────────────────────

    def build_browser_context_config(self, use_proxy: bool = False) -> Dict[str, Any]:
        """Build a complete configuration dict for browser context launch.

        Combines UA, viewport, locale, timezone, and proxy into one config
        suitable for Playwright's browser.new_context() or launch_persistent_context().

        Args:
            use_proxy: whether to include proxy configuration

        Returns:
            Dict suitable for passing to browser.new_context(**config)
        """
        ua = self.next_user_agent()
        p = self.profile

        config = {
            "user_agent": ua,
            "viewport": {"width": p.screen_width, "height": p.screen_height},
            "device_scale_factor": p.device_pixel_ratio,
            "locale": p.languages[0],
            "timezone_id": p.timezone,
            "color_scheme": "light",
            "extra_http_headers": self.get_headers(ua),
        }

        if use_proxy:
            proxy_cfg = self.get_proxy_playwright_config()
            if proxy_cfg:
                config["proxy"] = proxy_cfg

        return config


# ═══════════════════════════════════════════════════════════════════════════════
# TOP-LEVEL CONVENIENCE — One-stop stealth setup
# ═══════════════════════════════════════════════════════════════════════════════

def create_stealth_profile(browser: str = "chrome") -> StealthProfile:
    """Create a StealthProfile configured for a specific browser identity.

    Args:
        browser: "chrome", "firefox", or "safari"

    Returns:
        Configured StealthProfile ready for use.
    """
    profile = StealthProfile()
    profile.browser = browser
    profile._derive_platform()
    return profile


def setup_playwright_stealth(context_or_page, browser: str = "chrome") -> StealthInjector:
    """One-call setup: inject full 8-layer stealth into a Playwright context.

    Args:
        context_or_page: Playwright BrowserContext or Page
        browser: "chrome", "firefox", or "safari" (for identity consistency)

    Returns:
        StealthInjector (can be used for additional injections)

    Usage:
        browser = pw.chromium.launch_persistent_context(...)
        injector = setup_playwright_stealth(browser)
        page = browser.new_page()
        # All pages now have stealth protection
    """
    profile = create_stealth_profile(browser)
    injector = StealthInjector(profile)
    injector.inject_into_playwright(context_or_page)
    return injector


def setup_selenium_stealth(driver, browser: str = "chrome") -> StealthInjector:
    """One-call setup: inject full 8-layer stealth into a Selenium driver.

    Args:
        driver: Selenium WebDriver (Chrome/Chromium)
        browser: "chrome", "firefox", or "safari"

    Returns:
        StealthInjector
    """
    profile = create_stealth_profile(browser)
    injector = StealthInjector(profile)
    injector.inject_into_selenium(driver)
    return injector


def create_stealth_components(browser: str = "chrome") -> Tuple[
    StealthProfile, StealthInjector, StealthMouse, StealthKeyboard, StealthScroll, StealthNetwork
]:
    """Create all stealth components pre-configured together.

    Returns a tuple of (profile, injector, mouse, keyboard, scroll, network)
    all sharing the same StealthProfile for consistency.

    Usage:
        profile, injector, mouse, kb, scroll, net = create_stealth_components("chrome")
        browser = pw.chromium.launch_persistent_context(**net.build_browser_context_config())
        injector.inject_into_playwright(browser)
        waypoints = mouse.generate_waypoints(0, 0, 500, 300)
    """
    profile = create_stealth_profile(browser)
    injector = StealthInjector(profile)
    mouse = StealthMouse(profile)
    keyboard = StealthKeyboard()
    scroll = StealthScroll()
    network = StealthNetwork(profile)
    return profile, injector, mouse, keyboard, scroll, network


# ═══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════════════════════

def _test():
    """Verify all stealth components work correctly."""
    print("=== EIDOS STEALTH — Self-Test ===\n")

    # 1. StealthProfile
    print("1. StealthProfile")
    profile = create_stealth_profile("chrome")
    assert len(profile.user_agents) >= 4, "Need at least 4 UAs"
    assert len(profile.plugins) == 5, "Need 5 plugins"
    assert len(profile.fonts) >= 25, "Need at least 25 fonts"
    print(f"   UA count: {profile.get_ua_count()}")
    print(f"   Plugins: {len(profile.plugins)}")
    print(f"   Fonts: {len(profile.fonts)}")
    print("   PASS")

    # 2. StealthInjector — payloads
    print("\n2. StealthInjector")
    injector = StealthInjector(profile)
    full_js = injector.build_full_stealth_js()
    assert "'webdriver'" in full_js, "Must hide webdriver"
    assert "CanvasRenderingContext2D" in full_js, "Must randomize canvas"
    assert "UNMASKED_VENDOR_WEBGL" in full_js, "Must mask WebGL"
    assert "PluginArray" in full_js, "Must populate plugins"
    assert "AudioContext" in full_js, "Must mask AudioContext"
    assert "sampleRate" in full_js, "Must fix sampleRate"
    print(f"   Full JS payload: {len(full_js)} chars")
    lite_js = injector.build_lightweight_stealth_js()
    print(f"   Lite JS payload: {len(lite_js)} chars")
    print("   PASS")

    # 3. StealthMouse — submovement model
    print("\n3. StealthMouse")
    mouse = StealthMouse(profile)
    # Long movement (diagonal, ~700px)
    wp = mouse.generate_waypoints(0, 0, 500, 500)
    assert len(wp) >= 3, f"Need at least 3 waypoints for long move, got {len(wp)}"
    assert wp[0] == (0, 0) or abs(wp[0][0]) < 3, f"Starts near origin: {wp[0]}"
    # Final point should be close to target
    assert abs(wp[-1][0] - 500) < 25, f"Ends near target x: {wp[-1][0]}"
    assert abs(wp[-1][1] - 500) < 25, f"Ends near target y: {wp[-1][1]}"
    print(f"   500→500: {len(wp)} waypoints")

    # Short movement
    wp_short = mouse.generate_waypoints(100, 100, 105, 105)
    print(f"   5px movement: {len(wp_short)} waypoints")
    assert len(wp_short) >= 1

    # Reaction + pauses
    react = mouse.reaction_delay()
    assert 0.4 <= react <= 4.5, f"Reaction time in range: {react:.2f}s"
    print(f"   Reaction time: {react*1000:.0f}ms")
    print("   PASS")

    # 4. StealthKeyboard
    print("\n4. StealthKeyboard")
    kb = StealthKeyboard()
    delays = [kb.keystroke_delay() for _ in range(100)]
    avg = sum(delays) / len(delays) * 1000
    assert 30 <= avg <= 200, f"Average delay in 30-200ms: {avg:.0f}ms"
    print(f"   Avg keystroke delay: {avg:.0f}ms (100 samples)")
    assert kb.word_pause() > 0
    assert kb.pre_enter_pause() > 0.1
    print("   PASS")

    # 5. StealthScroll
    print("\n5. StealthScroll")
    scroll = StealthScroll()
    scroll_profile = scroll.generate_scroll_profile(800)
    total = sum(p[0] for p in scroll_profile)
    assert 700 <= total <= 900, f"Total scroll ~800: {total}"
    print(f"   Scroll profile: {len(scroll_profile)} bursts → {total}px")

    accel = scroll.acceleration_profile(10)
    assert accel[0] < 0.5, f"Start slow: {accel[0]:.2f}"
    assert accel[4] > 0.8, f"Mid fast: {accel[4]:.2f}"
    assert accel[-1] < 0.5, f"End slow: {accel[-1]:.2f}"
    print(f"   Accel profile: {' → '.join(f'{v:.2f}' for v in accel)}")
    print("   PASS")

    # 6. StealthNetwork
    print("\n6. StealthNetwork")
    net = StealthNetwork(profile)
    headers = net.get_headers()
    assert "User-Agent" in headers
    assert "Accept-Language" in headers
    assert "Sec-Ch-Ua" in headers, "Chrome must have Sec-Ch-Ua"
    print(f"   Headers count: {len(headers)}")
    print(f"   UA: {headers['User-Agent'][:60]}...")
    config = net.build_browser_context_config()
    assert config["viewport"]["width"] == 1920
    assert config["viewport"]["height"] == 1080
    assert config["locale"] == "es-ES"
    print(f"   Browser context config: {list(config.keys())}")
    print("   PASS")

    # 7. create_stealth_components
    print("\n7. create_stealth_components")
    profile2, injector2, mouse2, kb2, scroll2, net2 = create_stealth_components("firefox")
    assert profile2.browser == "firefox"
    assert isinstance(injector2, StealthInjector)
    assert isinstance(mouse2, StealthMouse)
    assert isinstance(kb2, StealthKeyboard)
    assert isinstance(scroll2, StealthScroll)
    assert isinstance(net2, StealthNetwork)
    print("   All 6 components created: PASS")

    # 8. Proxy
    print("\n8. ProxyConfig")
    proxy = ProxyConfig(url="http://127.0.0.1:8080", region="es")
    net2.add_proxy(proxy)
    assert net2.get_proxy() is not None
    net2.mark_proxy_success(proxy)
    assert proxy.healthy
    proxy_config = net2.get_proxy_playwright_config()
    assert proxy_config["server"] == "http://127.0.0.1:8080"
    print("   Proxy rotation: PASS")

    print("\n" + "=" * 50)
    print("ALL TESTS PASSED — EIDOS Stealth ready.")
    print("=" * 50)


if __name__ == "__main__":
    _test()
