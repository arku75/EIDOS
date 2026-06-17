"""
Config CLI - Gestión de configuración EIDOS
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import load_config, save_config, get_config_path, DEFAULT_CONFIG
from config.env_loader import ensure_eidos_home, load_eidos_dotenv

def config_cli():
    """Entry point para eidos-config"""
    parser = argparse.ArgumentParser(description="EIDOS Config CLI")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Init
    subparsers.add_parser("init", help="Initialize EIDOS config")
    
    # Show
    subparsers.add_parser("show", help="Show current config")
    
    # Get
    get_parser = subparsers.add_parser("get", help="Get config value")
    get_parser.add_argument("key", help="Config key (dot notation, e.g. gateway.port)")
    
    # Set
    set_parser = subparsers.add_parser("set", help="Set config value")
    set_parser.add_argument("key", help="Config key")
    set_parser.add_argument("value", help="Config value")
    
    # Env
    subparsers.add_parser("env", help="Show environment variables")
    
    args = parser.parse_args()
    
    if args.command == "init":
        home = ensure_eidos_home()
        config_path = get_config_path()
        
        if config_path.exists():
            print(f"⚠️ Config already exists: {config_path}")
            response = input("Overwrite? (y/N): ")
            if response.lower() != "y":
                print("Cancelled")
                sys.exit(0)
        
        save_config(DEFAULT_CONFIG)
        print(f"✅ Created default config: {config_path}")
        print(f"✅ EIDOS home: {home}")
        
    elif args.command == "show":
        config = load_config()
        import json
        print(json.dumps(config, indent=2))
        
    elif args.command == "get":
        config = load_config()
        keys = args.key.split(".")
        value = config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                print(f"Key not found: {args.key}")
                sys.exit(1)
        print(value)
        
    elif args.command == "set":
        config = load_config()
        keys = args.key.split(".")
        
        # Convert value
        val = args.value
        if val.lower() in ("true", "yes"):
            val = True
        elif val.lower() in ("false", "no"):
            val = False
        elif val.isdigit():
            val = int(val)
        
        # Set nested value
        target = config
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        target[keys[-1]] = val
        
        save_config(config)
        print(f"✅ Set {args.key} = {val}")
        
    elif args.command == "env":
        loaded = load_eidos_dotenv()
        print(f"Loaded env files: {[str(p) for p in loaded]}")
        print("\nEIDOS Environment:")
        for key, value in sorted(os.environ.items()):
            if key.startswith(("EIDOS_", "TELEGRAM_", "DISCORD_", "SLACK_", "OLLAMA_")):
                # Mask secrets
                if any(s in key for s in ["TOKEN", "KEY", "SECRET", "PASSWORD"]):
                    value = "***" if value else "(not set)"
                print(f"  {key}={value}")
    else:
        parser.print_help()

if __name__ == "__main__":
    config_cli()
