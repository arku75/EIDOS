import gi
gi.require_version('Gtk', '4.0')
gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, WebKit, GLib
import json

class EditorBridge:
    def __init__(self, webview):
        self.webview = webview
        # Registro del manejador de mensajes de JS a Python
        self.webview.get_user_content_manager().add_script_message_handler('editor')
        self.webview.connect("script-message-received::editor", self.on_message)
        self.agent_callback = None

    def on_message(self, user_content_manager, result):
        js_value = result.get_js_value()
        if js_value.is_object():
            # Extraer datos (En WebKit 6 se usa de forma distinta, ajustando a la recomendación de R1)
            # Para el prototipo, asumimos el JSON estructurado
            try:
                # Simulación de parsing (WebKitGTK 6 requiere manejo de JSCValue)
                msg = json.loads(js_value.to_json(0))
                if msg.get('type') == 'change':
                    if self.agent_callback:
                        self.agent_callback(msg.get('content'))
            except Exception as e:
                print(f"[Bridge Error] {e}")

    def set_agent_callback(self, callback):
        self.agent_callback = callback

    def set_text(self, text):
        self.webview.evaluate_javascript(f"window.editorAPI.setCode({json.dumps(text)})", -1, None, None, None, None)

    def insert_text(self, text):
        self.webview.evaluate_javascript(f"window.editorAPI.insertAtCursor({json.dumps(text)})", -1, None, None, None, None)
