import socket
import unittest
from unittest.mock import Mock

from protocol import decode
from tally_sender_ui import SenderUI, make_packet, validate_target


class SenderTests(unittest.TestCase):
    def test_click_sends_only_selected_id_and_holds_state(self):
        ui = SenderUI.__new__(SenderUI)
        ui.sock, ui.root, ui.status = Mock(), Mock(), Mock()
        ui.settings = Mock(return_value=(('127.0.0.1', 50080), 0, (True, True, True)))
        ui.active, ui.count = {2: 2}, 0
        ui.lamps = {i: Mock() for i in range(11)}
        ui.send({1: 1})
        ui.sock.sendto.assert_called_once()
        self.assertEqual(decode(ui.sock.sendto.call_args.args[0])[0]['id'], 1)
        self.assertEqual(ui.active, {1: 1, 2: 2})
        ui.root.after.assert_not_called()
        ui.sock.sendto.reset_mock()
        ui.send({1: 0})
        ui.sock.sendto.assert_called_once()
        self.assertEqual(ui.active, {1: 0, 2: 2})

    def test_all_ids_colors(self):
        for index in range(11):
            for color in range(3):
                item = decode(make_packet(index, color))[0]
                self.assertEqual((item['id'], item['screen'], item['brightness']), (index, 0, 3))
                self.assertEqual((item['left'], item['right'], item['text_color']), (color,) * 3)

    def test_channel_selection(self):
        item = decode(make_packet(0, 2, (False, True, False), 42))[0]
        self.assertEqual((item['left'], item['right'], item['text_color'], item['screen']), (0, 2, 0, 42))

    def test_target_validation(self):
        self.assertEqual(validate_target('127.0.0.1', '50080'), ('127.0.0.1', 50080))
        for ip, port in [('bad', 5), ('::1', 5), ('0.0.0.0', 5), ('224.0.0.1', 5),
                         ('127.0.0.1', 0), ('127.0.0.1', 65536)]:
            with self.assertRaises(ValueError):
                validate_target(ip, port)

    def test_udp_loopback(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            receiver.bind(('127.0.0.1', 0))
            receiver.settimeout(1)
            sender.sendto(make_packet(10, 1), receiver.getsockname())
            self.assertEqual(decode(receiver.recv(2048))[0]['left'], 1)
