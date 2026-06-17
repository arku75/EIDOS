from skills.plugin_manager import PluginManager
import sys

def verify_all():
    print("🔍 [SYSTEM CHECK] Verificando integridad de plugins...")
    pm = PluginManager()
    
    loaded = pm.list_plugins()
    expected = [
        "web_surfer", "gui_master", "personality", "malbot_core", 
        "dns_master", "traffic_gen", "tunnel_manager", "burp_driver",
        "terminal_god", "inspector", "dashboard", "voice_cortex", 
        "self_healer", "ear_cortex"
    ]
    
    missing = [p for p in expected if p not in loaded]
    
    if missing:
        print(f"❌ Plugins con errores o no cargados: {missing}")
        # Intentar cargar individualmente para ver el error
        for p in missing:
            print(f"\n--- Debugging {p} ---")
            pm.load_plugin(p)
    else:
        print(f"✅ Todos los sistemas nominales. ({len(loaded)} plugins activos)")
        print(f"   📋 Lista: {loaded}")

if __name__ == "__main__":
    verify_all()
