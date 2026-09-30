import copy
import json
from pathlib import Path
import socket
import struct
import tempfile
import time
import unittest
from app import DEFAULT, Router, validate
from protocol import decode, encode


def item(**kwargs):
    return dict(dict(screen=0, id=1, left=1, right=2, text_color=3,
                     brightness=3, text='CAM 1'), **kwargs)


def rule(**kwargs):
    return dict(dict(key='test', name='Test', source_ip='10.0.0.1', source_screen=0,
                     source_id=1, target_ip='127.0.0.1', target_port=45678,
                     target_screen=2, target_id=9, left_from='right', right_from='left',
                     text_from='text_color', label=None, mode='auto', manual=item(), repeat_ms=0), **kwargs)


class ProtocolTests(unittest.TestCase):
    def test_original_csharp_golden_packet(self):
        self.assertEqual(encode(item(text='TALLY', right=1, text_color=1)).hex(),
                         '0f00000000000100d500050054414c4c59')

    def test_unicode_roundtrip_and_maximum(self):
        self.assertEqual(decode(encode(item(text='摄像机🎥'))), [item(text='摄像机🎥')])
        self.assertEqual(len(encode(item(text='x'*2036))), 2048)
        with self.assertRaises(ValueError):
            encode(item(text='x'*2037))

    def test_multiple_displays(self):
        first, second = encode(item()), encode(item(id=2, text='CAM 2'))
        packet = first + second[6:]
        packet = struct.pack('<H', len(packet)-2) + packet[2:]
        self.assertEqual([x['id'] for x in decode(packet)], [1, 2])

    def test_rejects_truncation_and_control(self):
        packet = encode(item())
        for n in range(len(packet)):
            with self.assertRaises(ValueError):
                decode(packet[:n])
        for pos, value in ((3, 2), (9, 128)):
            data = bytearray(packet)
            data[pos] = value
            with self.assertRaises(ValueError):
                decode(data)


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.router = Router(Path(self.temp.name)/'config.json')
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver.bind(('127.0.0.1', 0))
        self.receiver.settimeout(.15)
        self.r = rule(target_port=self.receiver.getsockname()[1])
        self.router.config.update(output_ip='127.0.0.1', output_interface='', rules=[self.r])
        self.router.setup_sender()

    def tearDown(self):
        self.router.running = False
        if self.router.sender:
            self.router.sender.close()
        if self.router.listener:
            self.router.listener.close()
        self.receiver.close()
        self.temp.cleanup()

    def get(self):
        return decode(self.receiver.recv(4096))[0]

    def test_ip_filter_mapping_and_monitoring(self):
        self.router.ingest(encode(item()), ('10.0.0.2', 9000))
        self.assertEqual(len(self.router.inputs), 1)
        self.assertEqual(self.router.tx, 0)
        self.router.ingest(encode(item()), ('10.0.0.1', 9001))
        out = self.get()
        self.assertEqual((out['screen'], out['id'], out['left'], out['right']), (2, 9, 2, 1))

    def test_manual_cannot_be_overridden_and_resume_waits(self):
        self.router.action(dict(key='test', mode='manual', item=item(left=3)))
        self.assertEqual(self.get()['right'], 3)
        self.router.ingest(encode(item()), ('10.0.0.1', 9000))
        with self.assertRaises(socket.timeout):
            self.get()
        self.assertEqual(len(self.router.inputs), 1)
        self.router.action(dict(key='test', mode='auto'))
        with self.assertRaises(socket.timeout):
            self.get()
        self.router.ingest(encode(item()), ('10.0.0.1', 9000))
        self.assertEqual(self.get()['id'], 9)

    def test_pause_and_broadcast(self):
        self.router.config['forwarding'] = False
        self.router.ingest(encode(item()), ('10.0.0.1', 9000))
        self.assertEqual(self.router.tx, 0)
        self.router.config['forwarding'] = True
        self.router.ingest(encode(item(screen=65535, id=65535)), ('10.0.0.1', 9000))
        self.assertEqual(self.get()['id'], 9)

    def test_per_lamp_fanout_for_manual_and_auto(self):
        self.r.update(left_from='right', right_from='right', text_from='left')
        self.router.ingest(encode(item(left=1, right=2)), ('10.0.0.1', 9000))
        out = self.get()
        self.assertEqual((out['left'], out['right'], out['text_color']), (2, 2, 1))
        self.router.action(dict(key='test', mode='manual', item=item(left=3, right=1)))
        out = self.get()
        self.assertEqual((out['left'], out['right'], out['text_color']), (1, 1, 3))

    def test_blackout_overrides_fixed_colors(self):
        self.r.update(left_from='red', right_from='amber', text_from='green')
        self.router.action(dict(key='test', mode='manual', item=item(), blackout=True))
        out = self.get()
        self.assertEqual((out['left'], out['right'], out['text_color']), (0, 0, 0))

    def test_config_persists_and_duplicate_rejected(self):
        c = copy.deepcopy(self.router.config)
        self.router.save(c)
        self.assertEqual(Router(self.router.path).config, c)
        c['rules'].append(rule(key='duplicate', target_port=self.r['target_port']))
        with self.assertRaises(ValueError):
            validate(c)

    def test_occupied_port_keeps_original_config(self):
        c = copy.deepcopy(self.router.config)
        c['listen_port'] = self.receiver.getsockname()[1]
        with self.assertRaises(OSError):
            self.router.save(c)
        self.assertEqual(self.router.config['listen_port'], 40003)

    def test_repeat_holds_stale_input_until_explicit_off(self):
        import threading
        self.router.config['stale_seconds'] = 1
        self.r['repeat_ms'] = 100
        self.router.ingest(encode(item()), ('10.0.0.1', 9000))
        self.get()
        self.router.outputs['test']['input_time'] = time.time()-2
        worker = threading.Thread(target=self.router.repeat)
        worker.start()
        try:
            self.assertEqual(self.get()['left'], 2)
            self.assertEqual(self.get()['left'], 2)
            self.router.ingest(encode(item(left=0, right=0, text_color=0)), ('10.0.0.1', 9000))
            self.assertEqual(self.get()['left'], 0)
            self.router.outputs['test']['input_time'] = time.time()-60
            self.assertEqual(self.get()['left'], 0)
            with self.router.lock:
                self.router.config['forwarding'] = False
            with self.assertRaises(socket.timeout):
                self.get()
        finally:
            self.router.running = False
            worker.join()


if __name__ == '__main__':
    unittest.main()
