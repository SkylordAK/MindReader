"""
Muse S Simulator — OSC Sender
Simulates EEG data for testing Mind Reader.
"""

from pythonosc import udp_client
import numpy as np
import time
import argparse

# ──────────────────────── CONFIG ────────────────────────
IP = "127.0.0.1"
PORT = 5239
FS = 256
CHANNELS = 4

def generate_eeg_sample(t, state="neutral"):
    """
    Generate a sample with specific frequency characteristics.
    """
    # Base noise
    sample = np.random.normal(0, 5, CHANNELS)
    
    # Add Alpha (10Hz) for Relaxation/Zen
    if state == "calm":
        alpha = 50 * np.sin(2 * np.pi * 10 * t)
        sample += alpha
        
    # Add Beta (20Hz) for Focus/Anxiety
    elif state == "focus":
        beta = 40 * np.sin(2 * np.pi * 20 * t)
        sample += beta
        
    # Add Frontal Theta (6Hz) for Flow
    elif state == "flow":
        theta = 30 * np.sin(2 * np.pi * 6 * t)
        alpha = 20 * np.sin(2 * np.pi * 10 * t) # Moderate alpha
        sample[0:2] += theta # Frontal AF7, AF8
        sample += alpha
        
    # High Alpha on Right (AF8) for Positive Mood (Valence)
    elif state == "happy":
        alpha_right = 60 * np.sin(2 * np.pi * 10 * t)
        alpha_left = 10 * np.sin(2 * np.pi * 10 * t)
        sample[1] += alpha_right
        sample[0] += alpha_left
        
    return sample.tolist()

def run_simulator(mode="neutral", address="/muse/eeg"):
    client = udp_client.SimpleUDPClient(IP, PORT)
    print(f"Simulator started. Sending to {IP}:{PORT} (Mode: {mode}, Address: {address})")
    print("Press Ctrl+C to stop.")
    
    start_time = time.time()
    try:
        while True:
            t = time.time() - start_time
            sample = generate_eeg_sample(t, mode)
            client.send_message(address, sample)
            time.sleep(1/FS)
    except KeyboardInterrupt:
        print("\nSimulator stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, default="neutral", 
                        choices=["neutral", "calm", "focus", "flow", "happy"])
    parser.add_argument("--address", type=str, default="/muse/eeg",
                        help="OSC address to send to (default: /muse/eeg)")
    args = parser.parse_args()
    run_simulator(args.mode, args.address)
