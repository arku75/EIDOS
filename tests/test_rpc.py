import asyncio
import websockets
import json
import uuid

async def test_rpc(method, params=None):
    uri = "ws://127.0.0.1:18789"
    async with websockets.connect(uri) as websocket:
        # Auth challenge might be needed? 
        # Logs showed "device identity required" before, but I fixed it?
        # Let's see if we get a challenge or can just send commands.
        # OpenClaw usually requires an auth handshake.
        
        # Listen for welcome/challenge
        try:
            msg = await asyncio.wait_for(websocket.recv(), timeout=2.0)
            print(f"Received: {msg}")
        except asyncio.TimeoutError:
            print("No welcome message")

        # Send request
        req_id = str(uuid.uuid4())
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": req_id
        }
        print(f"Sending: {json.dumps(req)}")
        await websocket.send(json.dumps(req))

        # Wait for response
        try:
            while True:
                resp = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                print(f"Response: {resp}")
                data = json.loads(resp)
                if data.get("id") == req_id:
                    break
        except asyncio.TimeoutError:
            print("Timeout waiting for response")

async def main():
    print("--- Testing models.list ---")
    await test_rpc("models.list")
    
    print("\n--- Testing agent ---")
    await test_rpc("agent", {"prompt": "hello"})

    print("\n--- Testing llm-generate ---")
    await test_rpc("llm-generate", {"prompt": "hello"})

if __name__ == "__main__":
    asyncio.run(main())
