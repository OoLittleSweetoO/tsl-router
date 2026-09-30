"""Run on Pi as root while external sender sends test ID 60000 to UDP 40015/40016.
Uses an isolated instance on HTTP 18080 and target test port 49004.
"""
import copy
import json
import socket
import struct
import sys
import time
import urllib.request
import tempfile
import threading
from pathlib import Path
sys.path.insert(0, '/home/pi/tsl-router')
from protocol import decode
from app import Router, Handler, ThreadingHTTPServer, DEFAULT

temporary = tempfile.TemporaryDirectory(prefix='tsl-wire-')
test_router = Router(Path(temporary.name) / 'config.json')
test_router.config.update(listen_port=40015)
test_router.start()
httpd = ThreadingHTTPServer(('127.0.0.1', 18080), Handler)
httpd.router = test_router
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def state():
    return json.load(urllib.request.urlopen('http://127.0.0.1:18080/api/state', timeout=3))


def post(path, data):
    req = urllib.request.Request('http://127.0.0.1:18080/api/'+path,
                                 data=json.dumps(data).encode(),
                                 headers={'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=3))


original = state()['config']
config = copy.deepcopy(original)
manual = dict(screen=7, id=60001, left=3, right=0, text_color=2, brightness=3, text='手动验证')
rule = dict(key='wire-test', name='临时链路验证', source_ip='10.10.10.58', source_screen=0,
            source_id=60000, target_ip='192.168.10.151', target_port=49004,
            target_screen=7, target_id=60001, left_from='right', right_from='left',
            text_from='text_color', label='映射验证', mode='auto', manual=manual, repeat_ms=0)
config.update(listen_interface='wlan0', listen_port=40015, forwarding=True, rules=[rule])
capture = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
capture.bind(('eth0', 0))
capture.settimeout(.5)


def packet(expected_left):
    deadline = time.monotonic()+12
    while time.monotonic()<deadline:
        try:
            raw = capture.recv(65535)
        except socket.timeout:
            continue
        if len(raw)<42 or raw[12:14]!=b'\x08\x00' or raw[23]!=17:
            continue
        ihl = (raw[14]&15)*4
        offset = 14+ihl
        src, dst, length, _ = struct.unpack_from('!HHHH', raw, offset)
        if dst!=49004 or socket.inet_ntoa(raw[30:34])!='192.168.10.151':
            continue
        decoded = decode(raw[offset+8:offset+length])[0]
        if decoded['left']!=expected_left:
            continue
        assert socket.inet_ntoa(raw[26:30])=='192.168.10.2'
        assert decoded['id']==60001 and decoded['screen']==7
        print('CAPTURE eth0 192.168.10.2 -> 192.168.10.151:49004', decoded, flush=True)
        return
    raise AssertionError('No expected output on eth0')


try:
    post('config', config)
    print('READY external sender', flush=True)
    packet(2)
    post('action', dict(key='wire-test', mode='manual', item=manual))
    packet(3)
    count = state()['stats']['tx']
    rx = state()['stats']['rx']
    time.sleep(1)
    assert state()['stats']['rx']>rx
    assert state()['stats']['tx']==count
    print('PASS manual overrides continuous external input', flush=True)
    config['rules'][0]['mode']='disabled'
    config['listen_interface']='eth0'
    post('config', config)
    rx = state()['stats']['rx']
    time.sleep(1)
    assert state()['stats']['rx']==rx
    print('PASS eth0 listener excludes Wi-Fi packets', flush=True)
    config.update(listen_interface='wlan0', listen_port=40016)
    post('config', config)
    rx = state()['stats']['rx']
    time.sleep(1)
    assert state()['stats']['rx']>rx
    print('PASS live interface and port change', flush=True)
finally:
    post('config', original)
    capture.close()
    test_router.running = False
    httpd.shutdown()
    test_router.listener.close()
    test_router.sender.close()
    temporary.cleanup()
    print('CLOSED isolated test instance; production config untouched', flush=True)
