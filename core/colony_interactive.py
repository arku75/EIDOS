"""
EIDOS Colony Interactive Chat
=============================
Chat interactivo con la Colony Community.
"""

import sys
from pathlib import Path

# Add EIDOS to path
EIDOS_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(EIDOS_ROOT))

from core.colony_community import get_colony_community


def interactive_chat(model: str = "lfm2.5-thinking:1.2b"):
    """
    Inicia un chat interactivo con la Colony Community.
    
    Args:
        model: Modelo de Ollama a usar para las respuestas
    """
    print("=" * 60)
    print("  EIDOS COLONY CHAT")
    print("=" * 60)
    print(f"  Modelo: {model}")
    print("  Comandos: /agents, /status, /exit")
    print("=" * 60)
    
    community = get_colony_community()
    
    # Start session if not active
    if not getattr(community, '_session_active', False):
        community.start_session()
        print("\n✓ Sesión iniciada\n")
    
    while True:
        try:
            # Get user input
            user_input = input("\nTú: ").strip()
            
            if not user_input:
                continue
            
            # Handle commands
            if user_input == "/exit":
                print("Adiós!")
                break
            elif user_input == "/agents":
                agents = community.get_participants()
                print(f"\nAgentes ({len(agents)}):")
                for agent in agents:
                    print(f"  - {agent.get('name', 'Unknown')} ({agent.get('status', 'offline')})")
                continue
            elif user_input == "/status":
                print(f"\nEstado: {len(community.agents)} agentes activos")
                continue
            
            # Send message to colony
            print("\nPensando...")
            
        except KeyboardInterrupt:
            print("\nAdiós!")
            break
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    interactive_chat()
