"""
Mind Reader — WebSocket Bridge
Translates Muse S (OSC) data to a real-time WebSocket stream for the Web Dashboard.
"""

import asyncio
import json
import numpy as np
import time
from pythonosc import dispatcher, osc_server
from scipy.fft import rfft, rfftfreq
import websockets
import threading
from datetime import datetime

# ──────────────────────── CONFIG ────────────────────────
OSC_IP = "0.0.0.0"
OSC_PORT = 5239
WS_IP = "0.0.0.0"
WS_PORT = 8765

FS = 256
WINDOW_SEC = 2.0
STEP_SEC = 0.5
WINDOW_SAMPLES = int(FS * WINDOW_SEC)
STEP_SAMPLES = int(FS * STEP_SEC)

BANDS = {
    "Delta": (1, 4),
    "Theta": (4, 8),
    "Alpha": (8, 13),
    "Beta": (13, 30),
    "Gamma": (30, 45)
}

# ──────────────────────── STATE ─────────────────────────
class BridgeState:
    def __init__(self):
        self.buffer = np.empty((0, 4))
        self.clients = set()
        self.data_received = False
        self.loop = None # AsyncIO loop

    def get_band_power(self, data, low, high):
        yf = rfft(data)
        xf = rfftfreq(len(data), 1/FS)
        idx = np.logical_and(xf >= low, xf <= high)
        return float(np.mean(np.abs(yf[idx])**2)) if any(idx) else 0.0

    def process_window(self, window):
        af7 = window[:, 0]
        af8 = window[:, 1]
        all_channels = np.mean(window, axis=1)
        
        powers = {name: self.get_band_power(all_channels, *range_hz) 
                  for name, range_hz in BANDS.items()}
        
        # Metrics
        alpha_af8 = self.get_band_power(af8, *BANDS["Alpha"])
        alpha_af7 = self.get_band_power(af7, *BANDS["Alpha"])
        valence = float(np.log(alpha_af8 + 1e-6) - np.log(alpha_af7 + 1e-6))
        arousal = float(powers["Beta"] / (powers["Alpha"] + 1e-6))
        engagement = float(powers["Beta"] / (powers["Theta"] + powers["Alpha"] + 1e-6))
        
        frontal_theta = self.get_band_power((af7 + af8) / 2, *BANDS["Theta"])
        flow = bool((frontal_theta > 1.2 * powers["Theta"]) and (0.8 < powers["Alpha"] < 1.5))

        # Mood quadrants
        if valence > 0.1:
            mood = "Happy/Excited" if arousal > 1.0 else "Calm/Zen"
        else:
            mood = "Stressed/Anxious" if arousal > 1.0 else "Sad/Bored"

        return {
            "powers": powers,
            "valence": valence,
            "arousal": arousal,
            "engagement": engagement,
            "flow": flow,
            "mood": mood,
            "timestamp": datetime.now().isoformat()
        }

    def handle_eeg(self, address, *args):
        if not self.data_received:
            print(">>> [BRIDGE] Receiving EEG data!")
            self.data_received = True

        sample = np.array(args[:4]).reshape(1, 4)
        self.buffer = np.vstack([self.buffer, sample])
        
        if self.buffer.shape[0] >= WINDOW_SAMPLES:
            window = self.buffer[:WINDOW_SAMPLES]
            self.buffer = self.buffer[STEP_SAMPLES:]
            
            metrics = self.process_window(window)
            
            # Broadcast to all connected WebSocket clients
            if self.loop:
                asyncio.run_coroutine_threadsafe(self.broadcast(metrics), self.loop)

    async def broadcast(self, data):
        if self.clients:
            message = json.dumps(data)
            await asyncio.gather(*[client.send(message) for client in self.clients])

    async def ws_handler(self, websocket):
        self.clients.add(websocket)
        print(f">>> [WS] Client connected. Total: {len(self.clients)}")
        try:
            await websocket.wait_closed()
        finally:
            self.clients.remove(websocket)
            print(f">>> [WS] Client disconnected. Total: {len(self.clients)}")

async def main():
    state = BridgeState()
    state.loop = asyncio.get_running_loop()
    
    # Start OSC server in a thread
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", state.handle_eeg)
    disp.map("/eeg", state.handle_eeg)
    server = osc_server.ThreadingOSCUDPServer((OSC_IP, OSC_PORT), disp)
    
    osc_thread = threading.Thread(target=server.serve_forever, daemon=True)
    osc_thread.start()
    
    print(f"Bridge Active.")
    print(f"Listening for Muse S at {OSC_IP}:{OSC_PORT}")
    print(f"Broadcasting to WebSockets at ws://{WS_IP}:{WS_PORT}")
    
    async with websockets.serve(state.ws_handler, WS_IP, WS_PORT):
        await asyncio.Future()  # Run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping Bridge.")
