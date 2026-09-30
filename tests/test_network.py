import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import network_helper as net


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = patch.object(net, 'STATE', Path(self.temp.name))
        self.state.start()
        self.manager = net.Manager()
        self.request = dict(interface='eth0', method='manual', address='192.168.10.20/24', gateway='', dns='')
        self.previous = {'ipv4.method':'manual', 'ipv4.addresses':'192.168.10.2/24',
                         'ipv4.gateway':'', 'ipv4.dns':'', 'ipv4.never-default':'yes', 'connection.autoconnect':'yes'}

    def tearDown(self):
        self.state.stop()
        self.temp.cleanup()

    def prepare(self):
        with patch.object(net, 'devices', return_value={'eth0':'ethernet'}), \
             patch.object(net, 'profile', return_value=('test-uuid', True)), \
             patch.object(net, 'settings', return_value=copy.deepcopy(self.previous)), \
             patch.object(net.threading, 'Thread'):
            return self.manager.apply(self.request)

    def test_validation_rejects_bad_addresses_and_injection(self):
        for update in ({'address':'192.168.10.255/24'}, {'gateway':'10.0.0.1'},
                       {'address':'127.0.0.1/8'}, {'interface':'eth0; reboot'}):
            with self.assertRaises(ValueError):
                net.validate_request(dict(self.request, **update))

    def test_apply_journal_precedes_network_change(self):
        self.prepare()
        self.assertTrue(self.manager.journal.exists())
        with patch.object(net, 'nm', return_value='') as command, patch.object(net.time, 'sleep'):
            self.manager.activate()
            self.assertEqual(self.manager.pending['status'], 'awaiting-confirmation')
            self.assertIn('192.168.10.20/24', command.call_args_list[0].args)
        with self.assertRaises(ValueError):
            self.manager.confirm('wrong-token')
        self.manager.confirm(self.manager.pending['id'])
        self.assertIsNone(self.manager.pending)
        self.assertTrue((Path(self.temp.name)/'last-confirmed.json').exists())

    def test_rollback_restores_original_profile(self):
        self.prepare()
        with patch.object(net, 'nm', return_value='') as command:
            self.manager.rollback()
            self.assertIn('192.168.10.2/24', command.call_args_list[0].args)
            self.assertEqual(command.call_args_list[1].args, ('connection','up','uuid','test-uuid','ifname','eth0'))
        self.assertIsNone(self.manager.pending)

    def test_restart_recovers_unconfirmed_change(self):
        self.prepare()
        with patch.object(net, 'nm', return_value='') as command:
            recovered = net.Manager()
            self.assertIn('192.168.10.2/24', command.call_args_list[0].args)
            self.assertIsNone(recovered.pending)

    def test_activation_failure_rolls_back(self):
        self.prepare()
        with patch.object(net, 'nm', side_effect=[ValueError('failure'), '', '']), patch.object(net.time, 'sleep'):
            self.manager.activate()
        self.assertIsNone(self.manager.pending)
        self.assertIn('Rolled back', self.manager.message)


if __name__ == '__main__':
    unittest.main()
