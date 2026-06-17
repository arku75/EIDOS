#!/usr/bin/env python3
"""
EIDOS test_bridge.py
Prueba la conexión al eidos_bridge_server enviando un payload JSON
por el socket Unix y esperando la respuesta.
"""
import socket
import json
import sys
import time

SOCKET_PATH = "/tmp/eidos-dispatcher.sock"

def test_bridge(text: str, channel: str = "telegram"):
    print(f"📡 Conectando a {SOCKET_PATH}...")
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
    except Exception as e:
        print(f"❌ Error conectando al bridge (¿está eidos_bridge_server.py corriendo?): {e}")
        return

    req = {
        "id": f"test_{int(time.time())}",
        "text": text,
        "channel": channel,
        "user_id": "999"
    }

    payload = json.dumps(req) + "\n"
    print(f"📤 Enviando: {req}")
    sock.sendall(payload.encode())

    print("⏳ Esperando respuesta...")
    resp_data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        resp_data += chunk
        if b"\n" in chunk:
            break

    sock.close()

    try:
        resp = json.loads(resp_data.decode().strip())
        print(f"⚖️  Riesgo detectado: {resp.get('risk', 0)}/10")
        print(f"🔒 Requiere Approval: {resp.get('requires_approval', False)}")
        print(f"⚡ Exec CMD propuesto: {resp.get('exec_cmd', 'Ninguno')}")
        print(f"🤖 EIDOS: {resp.get('text', '')}")
    except json.JSONDecodeError:
        print(f"❌ Respuesta inválida: {resp_data.decode()}")

if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "¿Cuanta RAM hay libre?"
    test_bridge(msg)
