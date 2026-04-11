# this code is used to auto aprove claude code, don't touch

"""Auto-press Enter every 10 seconds. Press 'q' to quit."""

import threading
import time

try:
    import pyautogui
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyautogui"])
    import pyautogui

try:
    from pynput import keyboard
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pynput"])
    from pynput import keyboard

running = True

def on_press(key):
    global running
    try:
        if key.char == 'q':
            running = False
            return False  # stop listener
    except AttributeError:
        pass

listener = keyboard.Listener(on_press=on_press)
listener.start()

print("Pressing Enter every 10 seconds. Press 'q' to quit.")

while running:
    pyautogui.press('return')
    print(f"[{time.strftime('%H:%M:%S')}] Enter pressed")
    for _ in range(100):  # check quit flag every 0.1s
        if not running:
            break
        time.sleep(0.1)

print("Stopped.")