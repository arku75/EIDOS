"""
core/eidos_voice.py — Voz para EIDOS (Vosk speech-to-text)
===========================================================
Permite a EIDOS escuchar comandos de voz y responder.
Usa Vosk (offline, sin internet) para reconocimiento.

Modelos instalados:
  - ES: vosk-model-small-es-0.42 (42MB)
  - EN: vosk-model-small-en-us-0.15 (15MB)

Activación: EIDOS_VOICE=1 python3 core/eidos_voice.py listen
"""
import json, os, sys, time, logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("eidos.voice")

VOSK_MODEL_ES = Path.home() / ".eidos" / "models" / "vosk-model-small-es-0.42"
VOSK_MODEL_EN = Path.home() / ".eidos" / "models" / "vosk-model-small-en-us-0.15"

def listen(lang: str = "es", timeout: int = 30) -> Optional[str]:
    """Escucha el micrófono y devuelve texto reconocido."""
    try:
        import vosk
        import pyaudio
    except ImportError:
        log.warning("vosk o pyaudio no instalados. pip install vosk pyaudio")
        return None
    
    model_path = VOSK_MODEL_ES if lang == "es" else VOSK_MODEL_EN
    if not model_path.exists():
        log.warning(f"Modelo {lang} no encontrado en {model_path}")
        return None
    
    model = vosk.Model(str(model_path))
    recognizer = vosk.KaldiRecognizer(model, 16000)
    
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                    input=True, frames_per_buffer=8000)
    stream.start_stream()
    
    log.info(f"🎤 Escuchando ({lang})...")
    deadline = time.time() + timeout
    
    while time.time() < deadline:
        data = stream.read(4000, exception_on_overflow=False)
        if recognizer.AcceptWaveform(data):
            result = json.loads(recognizer.Result())
            text = result.get("text", "").strip()
            if text:
                stream.stop_stream()
                stream.close()
                p.terminate()
                return text
    
    stream.stop_stream()
    stream.close()
    p.terminate()
    return None

def speak(text: str, lang: str = "es") -> bool:
    """EIDOS habla usando espeak (síntesis de voz offline)."""
    import subprocess
    try:
        voice = "es" if lang == "es" else "en"
        subprocess.run(["espeak", "-v", voice, text], 
                      timeout=10, capture_output=True)
        return True
    except Exception as e:
        log.warning(f"speak error: {e}")
        return False

def listen_and_respond(lang: str = "es", timeout: int = 30) -> Optional[str]:
    """Escucha, procesa con DeepSeek, y responde con voz."""
    text = listen(lang, timeout)
    if not text:
        return None
    
    from core.eidos_learn import ask_llm
    answer, source = ask_llm(f"Responde en una frase en español: {text}", timeout=15)
    
    if answer:
        speak(answer, lang)
        return answer
    return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "listen"
    lang = sys.argv[2] if len(sys.argv) > 2 else "es"
    
    if cmd == "listen":
        text = listen(lang, timeout=15)
        print(f"🎤 {text}" if text else "⚠️ No se detectó voz")
    elif cmd == "speak":
        speak(" ".join(sys.argv[2:]) if len(sys.argv) > 2 else "Hola, soy EIDOS")
    elif cmd == "chat":
        result = listen_and_respond(lang)
        print(f"💬 {result}" if result else "⚠️ Sin respuesta")
