import asyncio
import json
import websockets
import time
from pythonosc import udp_client
import numpy as np

WS_URL = "ws://localhost:8765"
OSC_IP = "127.0.0.1"
OSC_PORT = 5239

async def send_osc_data():
    client = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)
    print(">>> Starting OSC Simulation...")
    for _ in range(50): # Send 50 samples (~200ms)
        sample = np.random.normal(0, 10, 4).tolist()
        client.send_message("/muse/eeg", sample)
        await asyncio.sleep(0.01)

async def test_multi_client():
    print(">>> Connecting Client A and Client B...")
    try:
        async with websockets.connect(WS_URL) as ws_a, \
                   websockets.connect(WS_URL) as ws_b:
                   
            # Identify
            await ws_a.send(json.dumps({"type": "IDENTIFY", "username": "UserA", "user_id": 1}))
            await ws_b.send(json.dumps({"type": "IDENTIFY", "username": "UserB", "user_id": 2}))
            
            print(">>> Disabling ML for Client A...")
            await ws_a.send(json.dumps({"type": "TOGGLE_ML", "value": False}))
            
            # Start OSC data in background
            osc_task = asyncio.create_task(send_osc_data())
            
            # Wait for data
            print(">>> Waiting for broadcasted data...")
            num_received = 0
            while num_received < 3:
                msg_a = await asyncio.wait_for(ws_a.recv(), timeout=5)
                msg_b = await asyncio.wait_for(ws_b.recv(), timeout=5)
                
                data_a = json.loads(msg_a)
                data_b = json.loads(msg_b)
                
                print(f"\n[Data Received]")
                print(f"Client A ML Enabled: {data_a.get('ml_enabled')} | Mood: {data_a.get('mood')}")
                print(f"Client B ML Enabled: {data_b.get('ml_enabled')} | Mood: {data_b.get('mood')}")
                
                if data_a.get('ml_enabled') is False and data_b.get('ml_enabled') is True:
                    print("SUCCESS: Isolation verified (A False, B True)")
                else:
                    print("FAILURE: State leaked!")
                
                num_received += 1
            
            await osc_task
            print("\n>>> Verification Complete.")

    except Exception as e:
        print(f">>> Verification Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_multi_client())
