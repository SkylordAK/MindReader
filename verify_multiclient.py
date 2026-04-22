import asyncio
import json
import websockets
import time

WS_URL = "ws://localhost:8765"

async def test_multi_client():
    print(">>> Connecting Client A and Client B...")
    async with websockets.connect(WS_URL) as ws_a, \
               websockets.connect(WS_URL) as ws_b:
               
        # Identify
        await ws_a.send(json.dumps({"type": "IDENTIFY", "username": "UserA", "user_id": 1}))
        await ws_b.send(json.dumps({"type": "IDENTIFY", "username": "UserB", "user_id": 2}))
        
        print(">>> Disabling ML for Client A...")
        await ws_a.send(json.dumps({"type": "TOGGLE_ML", "value": False}))
        
        # Wait for some data to arrive
        print(">>> Waiting for data...")
        
        # We need a few samples to be sure
        for i in range(5):
            msg_a = await ws_a.recv()
            msg_b = await ws_b.recv()
            
            data_a = json.loads(msg_a)
            data_b = json.loads(msg_b)
            
            print(f"\n[Iteration {i}]")
            print(f"Client A Mood: {data_a.get('mood')} | ML Enabled: {data_a.get('ml_enabled')}")
            print(f"Client B Mood: {data_b.get('mood')} | ML Enabled: {data_b.get('ml_enabled')}")
            
            # Assertions (optional in script, but good for logs)
            if data_a.get('ml_enabled') is False and data_b.get('ml_enabled') is True:
                print("PASSED: Isolation works (A is disabled, B is enabled)")
            else:
                print("FAILED: Isolation issue!")
                
            if data_a.get('mood') == "ML DISABLED":
                print("PASSED: Client A mood is correctly updated to 'ML DISABLED'")
            
            # Check dB ranges
            if "powers" in data_b:
                avg_p = sum(data_b["powers"].values()) / len(data_b["powers"])
                print(f"Client B Avg Power (dB): {avg_p:.2f}")
                if -20 <= avg_p <= 100: # Broad range for dB
                    print("PASSED: dB values in expected broad range")

if __name__ == "__main__":
    try:
        asyncio.run(test_multi_client())
    except Exception as e:
        print(f"Error during verification: {e}")
