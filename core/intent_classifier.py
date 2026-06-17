import urllib.request
import json
import logging
import os

class IntentClassifier:
    """
    Clasificador ultrarrápido de intenciones para EIDOS.
    Utiliza un modelo local ligero (lfm2.5-thinking:1.2b) para clasificar el 
    texto de entrada en [CHAT], [EXEC], [PLAN] o [VISION].
    """
    def __init__(self, ollama_url: str | None = None, model: str = "lfm2.5-thinking:1.2b"):
        if ollama_url is None:
            self.ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        else:
            self.ollama_url = ollama_url
            
        self.model = model
        self.logger = logging.getLogger("IntentClassifier")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            ch = logging.StreamHandler()
            ch.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(ch)

    def classify(self, text: str) -> str:
        """
        Analiza el texto y retorna una de las 4 intenciones strictly:
        'CHAT', 'EXEC', 'PLAN', 'VISION'.
        """
        prompt = (
            "You are an Intent Classifier for the EIDOS autonomous system.\n"
            "Analyze the user's input and classify it into EXACTLY ONE of these categories:\n"
            "- CHAT: The user is asking a conversational question, asking for knowledge, or saying hello.\n"
            "- EXEC: The user wants to execute a direct shell command, script, system action, or run a tool.\n"
            "- PLAN: The user is asking for a complex multi-step task, goal-oriented research, or meta-orchestration.\n"
            "- VISION: The user is asking to look at the screen, analyze an image, or use spatial awareness.\n\n"
            f"User Input: \"{text}\"\n\n"
            "REPLY ONLY WITH THE CATEGORY WORD (CHAT, EXEC, PLAN, VISION). DO NOT ADD ANY OTHER TEXT."
        )

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 10
            }
        }
        
        try:
            req = urllib.request.Request(
                f"{self.ollama_url}/api/generate",
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                answer = result.get("response", "").strip().upper()
                
                # Normalizar respuesta robusta
                if "EXEC" in answer: return "EXEC"
                if "PLAN" in answer: return "PLAN"
                if "VISION" in answer: return "VISION"
                return "CHAT" # Default fallback
        except Exception as e:
            self.logger.error(f"Error clasificando intención: {e}")
            return "CHAT" # Fallback conservador
