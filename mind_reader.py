"""
Mind Reader — Core EEG Engine
Real-time Mind State Prediction using Muse S (OSC).

Features:
- 30s Discard -> 60s Baseline -> Live Mode
- Advanced Heuristics (Flow, Engagement, Mood Matrix)
- Real-time Matplotlib Dashboard
"""

import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from pythonosc import dispatcher, osc_server
from scipy.fft import rfft, rfftfreq
from datetime import datetime
import os
import time
import threading
from queue import Queue

# ──────────────────────── CONFIG ────────────────────────
IP = "0.0.0.0"  # Listen on all interfaces
PORT = 5239
FS = 256        # Muse S sampling rate
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
class MindState:
    DISCARD = 0
    BASELINE = 1
    LIVE = 2

class MindReader:
    def __init__(self):
        self.state = MindState.DISCARD
        self.start_time = time.time()
        self.buffer = np.empty((0, 4)) # AF7, AF8, TP9, TP10
        self.baseline_powers = None # Will store avg band powers
        self.history = [] # For logging
        self.data_received = False
        self.queue = Queue() # For passing results to main thread
        
        # Timing constants
        self.discard_duration = 30
        self.baseline_duration = 60
        self.history_len = 50 # Points to show on graph
        self.band_history = {name: [0]*self.history_len for name in BANDS}
        
        # Logging Setup
        os.makedirs("Logs", exist_ok=True)
        self.log_file = f"Logs/mind_session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self.header = "timestamp,state,Delta,Theta,Alpha,Beta,Gamma,Valence,Arousal,Engagement,Flow\n"
        with open(self.log_file, "w") as f:
            f.write(self.header)
        
        # Setup Plotting
        plt.ion()
        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(10, 8))
        self.band_lines = {name: self.ax1.plot(range(self.history_len), [0]*self.history_len, label=name)[0] for name in BANDS}
        self.ax1.legend(loc='upper right')
        self.ax1.set_title("Real-time Band Powers")
        self.ax1.set_ylim(0, 500) # Initial guess for power scale
        self.state_text = self.ax2.text(0.5, 0.5, "Initializing...", 
                                        fontsize=20, ha='center', va='center', weight='bold')
        self.ax2.axis('off')
        plt.show(block=False)

    def get_band_power(self, data, low, high):
        """Extract average power in a specific frequency band."""
        yf = rfft(data)
        xf = rfftfreq(len(data), 1/FS)
        idx = np.logical_and(xf >= low, xf <= high)
        return np.mean(np.abs(yf[idx])**2) if any(idx) else 0

    def process_window(self, window):
        """Calculate all metrics for the current window."""
        # Channels: AF7 (0), AF8 (1), TP9 (2), TP10 (3)
        af7 = window[:, 0]
        af8 = window[:, 1]
        all_channels = np.mean(window, axis=1)
        
        powers = {name: self.get_band_power(all_channels, *range_hz) 
                  for name, range_hz in BANDS.items()}
        
        # Advanced Metrics
        # 1. Frontal Alpha Asymmetry (Mood/Valence)
        # Using ln is standard to normalize power
        alpha_af8 = self.get_band_power(af8, *BANDS["Alpha"])
        alpha_af7 = self.get_band_power(af7, *BANDS["Alpha"])
        valence = np.log(alpha_af8 + 1e-6) - np.log(alpha_af7 + 1e-6)
        
        # 2. Arousal (Intensity)
        arousal = powers["Beta"] / (powers["Alpha"] + 1e-6)
        
        # 3. Engagement Index
        engagement = powers["Beta"] / (powers["Theta"] + powers["Alpha"] + 1e-6)
        
        # 4. Flow State (Frontal Theta + Moderate Alpha)
        frontal_theta = self.get_band_power((af7 + af8) / 2, *BANDS["Theta"])
        flow = (frontal_theta > 1.2 * powers["Theta"]) and (0.8 < powers["Alpha"] < 1.5) # Heuristic

        return powers, valence, arousal, engagement, flow

    def update_ui(self, powers, mood, engagement, flow):
        # Update Band History
        for name, val in powers.items():
            self.band_history[name].append(val)
            self.band_history[name].pop(0)
            self.band_lines[name].set_ydata(self.band_history[name])
        
        # Auto-scale Y axis
        max_val = max([max(h) for h in self.band_history.values()])
        self.ax1.set_ylim(0, max_val * 1.2)
        
        # Update Text
        color = 'green' if flow else 'black'
        self.state_text.set_text(f"Mood: {mood}\nEngagement: {engagement:.2f}\nFlow: {'🔥 YES 🔥' if flow else 'no'}")
        self.state_text.set_color(color)
        
        self.state_text.set_color(color)
        self.fig.canvas.draw_idle()
        plt.pause(0.01)

    def log_data(self, powers, v, a, e, f):
        state_str = ["DISCARD", "BASELINE", "LIVE"][self.state]
        ts = datetime.now().isoformat()
        row = f"{ts},{state_str},{powers['Delta']:.2f},{powers['Theta']:.2f},{powers['Alpha']:.2f},{powers['Beta']:.2f},{powers['Gamma']:.2f},{v:.3f},{a:.3f},{e:.3f},{f}\n"
        with open(self.log_file, "a") as f_log:
            f_log.write(row)

    def get_mood_string(self, valence, arousal):
        if valence > 0.1: # Positive
            return "Happy/Excited" if arousal > 1.0 else "Calm/Zen"
        else: # Negative
            return "Stressed/Anxious" if arousal > 1.0 else "Sad/Bored"

    def handle_eeg(self, address, *args):
        if not self.data_received:
            print("\n>>> [SUCCESS] Receiving EEG data from Muse S!")
            self.data_received = True
            
        sample = np.array(args[:4]).reshape(1, 4)
        self.buffer = np.vstack([self.buffer, sample])
        
        elapsed = time.time() - self.start_time
        
        # State Transitions
        if self.state == MindState.DISCARD and elapsed > self.discard_duration:
            print(">>> Discard phase complete. Starting BASELINE...")
            self.state = MindState.BASELINE
            self.start_time = time.time()
            self.buffer = np.empty((0, 4))
            
        elif self.state == MindState.BASELINE and elapsed > self.baseline_duration:
            print(">>> Baseline phase complete. GOING LIVE!")
            # Calculate average baseline here (optional refinement)
            self.state = MindState.LIVE
            self.start_time = time.time()
            
        # Processing
        if self.buffer.shape[0] >= WINDOW_SAMPLES:
            window = self.buffer[:WINDOW_SAMPLES]
            self.buffer = self.buffer[STEP_SAMPLES:]
            
            p, v, a, e, f = self.process_window(window)
            mood = self.get_mood_string(v, a)
            self.log_data(p, v, a, e, f)
            
            if self.state == MindState.LIVE:
                status = f"LIVE | MOOD: {mood: <15} | ENGAGE: {e:.2f} | FLOW: {f}"
                print(status, end='\r')
            else:
                phase = "DISCARD" if self.state == MindState.DISCARD else "BASELINE"
                rem = (self.discard_duration if self.state == MindState.DISCARD else self.baseline_duration) - elapsed
                print(f"[{phase}] Calibrating... {rem:.1f}s remaining", end='\r')
                mood = f"CALIBRATING ({phase})"
            
            # Put data in queue for main thread UI update
            self.queue.put((p, mood, e, f))

def run_app():
    reader = MindReader()
    disp = dispatcher.Dispatcher()
    disp.map("/muse/eeg", reader.handle_eeg)
    disp.map("/eeg", reader.handle_eeg)
    
    server = osc_server.ThreadingOSCUDPServer((IP, PORT), disp)
    
    # Start OSC server in background thread
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    print(f"Mind Reader active. Listening on {IP}:{PORT}...")
    print("Press Ctrl+C to stop.")
    
    try:
        while True:
            # Check for new data in the queue
            if not reader.queue.empty():
                p, mood, e, f = reader.queue.get()
                reader.update_ui(p, mood, e, f)
            plt.pause(0.05) # Tiny sleep to keep UI responsive
    except KeyboardInterrupt:
        print("\nStopping Mind Reader.")
        server.shutdown()

if __name__ == "__main__":
    run_app()
