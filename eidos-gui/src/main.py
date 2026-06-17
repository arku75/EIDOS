import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('WebKit', '6.0')
from gi.repository import Gtk, WebKit, Gio
import os

from .editor.bridge import EditorBridge

class EidosMainWindow(Gtk.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("EIDOS Tactical IDE - Antigravity Engine")
        self.set_default_size(1200, 800)

        # Main Layout
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(self.main_box)

        # HeaderBar (Hacker Style)
        header = Gtk.HeaderBar()
        self.set_titlebar(header)
        
        # WebView Setup
        self.webview = WebKit.WebView()
        self.bridge = EditorBridge(self.webview)
        
        # Load local index.html
        current_dir = os.path.dirname(os.path.abspath(__file__))
        index_path = f"file://{os.path.join(current_dir, 'assets/web/index.html')}"
        self.webview.load_uri(index_path)

        # Add to layout
        self.main_box.append(self.webview)

        # DevTools enabled
        self.webview.get_settings().set_enable_developer_extras(True)

class EidosGUI(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.ser.eidos.gui",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = EidosMainWindow(application=self)
        win.present()

if __name__ == "__main__":
    app = EidosGUI()
    app.run(sys.argv)
