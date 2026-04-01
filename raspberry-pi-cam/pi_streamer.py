import socket
import threading
import time
import numpy as np
import sounddevice as sd
import cv2
import requests
import os
import subprocess

# Surpress OpenCV warnings about V4L2 backend on RPi
os.environ["OPENCV_LOG_LEVEL"] = "ERROR"

# --- CONFIGURATION ---
SERVER_IP = "cam.home.local"  # Change to your server's IP or hostname
AUDIO_PORT = 6002
PHOTO_URL = f"http://{SERVER_IP}:6003/upload_photo"

SAMPLE_RATE = 16000
CHANNELS = 1
PHOTO_INTERVAL = 20

# --- HELPER FUNCTIONS ---

def setup_mic_volume():
    """Try to set microphone volume to max Gain and enable Capture."""
    try:
        # Set 'Mic' or 'Capture' to 100% for card 2 (mine C525)
        subprocess.run(["amixer", "-c", "2", "set", "Mic", "100%", "unmute"], capture_output=True)
        subprocess.run(["amixer", "-c", "2", "set", "Capture", "100%", "cap"], capture_output=True)
        print("[+] Microphone volume (Card 2) set to 100%.")
    except:
        pass

def find_logitech_mic():
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if "C525" in dev['name'] or "Logitech" in dev['name']:
            if dev['max_input_channels'] > 0:
                return i
    return None

# --- 1. AUDIO STREAM ---

def audio_stream_thread():
    setup_mic_volume()
    while True:
        mic_id = find_logitech_mic()
        if mic_id is None:
            print("[!] Logitech C525 microphone not found, trying again...")
            time.sleep(5)
            continue

        try:
            print(f"[*] Connecting AUDIO (ID:{mic_id}) to {SERVER_IP}...")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((SERVER_IP, AUDIO_PORT))
            s.settimeout(None)
            print("[+] Audio socket connected.")

            def callback(indata, frames, time, status):
                # Amplification x4 for better intelligibility
                audio_int16 = (indata * 32767 * 4).clip(-32768, 32767).astype(np.int16)
                try:
                    s.sendall(audio_int16.tobytes())
                except:
                    raise sd.CallbackStop

            with sd.InputStream(device=mic_id, samplerate=SAMPLE_RATE,
                                channels=CHANNELS, dtype='float32', callback=callback):
                while True:
                    time.sleep(1)
        except Exception as e:
            print(f"[!] Audio error: {e}")
            time.sleep(5)
        finally:
            try: s.close()
            except: pass

# --- 2. PHOTO THREAD ---

def photo_thread():
    print("[*] Starting PHOTO thread...")
    while True:
        # C525 on RPi often requires V4L2 backend
        cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(1, cv2.CAP_V4L2)

        if not cap.isOpened():
            print("[!] Camera unavailable, waiting...")
            time.sleep(10)
            continue

        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        while True:
            try:
                # Clear buffer for fresh photo
                for _ in range(3): cap.grab()
                ret, frame = cap.read()

                if ret:
                    _, img_bytes = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                    files = {'photo': ('photo.jpg', img_bytes.tobytes(), 'image/jpeg')}
                    r = requests.post(PHOTO_URL, files=files, auth=('admin', 'admin'), timeout=5)
                    print(f"[+] Photo OK ({r.status_code})")
                else:
                    break # Restart camera
            except Exception as e:
                print(f"[!] Photo error: {e}")
                break # Restart camera

            time.sleep(PHOTO_INTERVAL)

        cap.release()
        time.sleep(2)

if __name__ == '__main__':
    # Run in 2 separate threads to ensure audio and photo capture run independently
    threading.Thread(target=audio_stream_thread, daemon=True).start()
    photo_thread()