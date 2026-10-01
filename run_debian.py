"""Supervise the Python router and Node Ember+ provider on Linux."""
import signal
import subprocess
import sys
import time

children = []
stopping = False


def stop(*_):
    global stopping
    stopping = True
    for child in children:
        if child.poll() is None:
            child.terminate()


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    children.append(subprocess.Popen([sys.executable, 'app.py']))
    children.append(subprocess.Popen(['node', 'ember/provider.js']))
    while not stopping and all(child.poll() is None for child in children):
        time.sleep(.25)
finally:
    failed = not stopping
    stop()
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
sys.exit(1 if failed else 0)
