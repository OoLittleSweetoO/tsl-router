import unittest
from app import DEFAULT, validate


class EmberConfigTests(unittest.TestCase):
    def test_legacy_config_defaults_disabled(self):
        self.assertFalse(validate(DEFAULT)['ember']['enabled'])

    def test_invalid_config(self):
        for changes in ({'enabled': 1}, {'screen': 65535}, {'count': 0}, {'count': 257}, {'source_ip': 'bad'}):
            c = validate(DEFAULT)
            c['ember'].update(changes)
            with self.assertRaises(ValueError):
                validate(c)

    def test_independent_of_udp_forwarding(self):
        c = validate(DEFAULT)
        c['forwarding'] = False
        c['ember']['enabled'] = True
        self.assertTrue(validate(c)['ember']['enabled'])
