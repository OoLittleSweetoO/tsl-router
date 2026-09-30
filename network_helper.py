"""Narrow host-side NetworkManager bridge, accessible only through a Unix socket."""
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler

FIELDS = ('ipv4.method', 'ipv4.addresses', 'ipv4.gateway', 'ipv4.dns',
          'ipv4.never-default', 'connection.autoconnect')
STATE = Path(os.environ.get('NETWORK_STATE', '/var/lib/tsl-network'))
SOCKET = os.environ.get('NETWORK_SOCKET', '/run/tsl-network/control.sock')


def nm(*args):
    p = subprocess.run(['nmcli', '--wait', '15', *args], capture_output=True,
                       text=True, timeout=22, env=dict(os.environ, LC_ALL='C'))
    if p.returncode:
        raise ValueError(p.stderr.strip() or 'NetworkManager command failed')
    return p.stdout.strip()


def devices():
    return {line.split(':')[0]: line.split(':')[1]
            for line in nm('-t', '-f', 'DEVICE,TYPE', 'device').splitlines()
            if len(line.split(':')) == 2 and line.split(':')[1] in ('ethernet', 'wifi')
            and not line.split(':')[0].startswith(('veth', 'docker', 'br-'))}


def profile(interface):
    active = nm('-g', 'GENERAL.CON-UUID', 'device', 'show', interface)
    if active and active != '--':
        return active, True
    matches = []
    for line in nm('-t', '-f', 'UUID,TYPE', 'connection', 'show').splitlines():
        key, kind = line.split(':', 1)
        if kind not in ('802-3-ethernet', '802-11-wireless'):
            continue
        if nm('-g', 'connection.interface-name', 'connection', 'show', 'uuid', key) == interface:
            matches.append(key)
    if len(matches) > 1:
        raise ValueError('该网口有多个配置，请先通过 SSH 选择 / Multiple profiles; select one via SSH')
    return (matches[0] if matches else None), False


def settings(key):
    return {field: nm('-g', field, 'connection', 'show', 'uuid', key) for field in FIELDS}


def validate_request(data):
    name = data['interface']
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', name):
        raise ValueError('网口名称无效 / Invalid interface')
    mode = data['method']
    if mode not in ('manual', 'auto'):
        raise ValueError('请选择静态 IP 或 DHCP / Select static or DHCP')
    result = {'interface': name, 'method': mode, 'address': '', 'gateway': '', 'dns': ''}
    if mode == 'manual':
        address = ipaddress.IPv4Interface(data['address'].strip())
        if address.ip.is_unspecified or address.ip.is_multicast or address.ip.is_loopback:
            raise ValueError('请填写网口单播地址 / Use a unicast interface address')
        if address.network.prefixlen < 31 and address.ip in (address.network.network_address, address.network.broadcast_address):
            raise ValueError('不能使用网络或广播地址 / Network or broadcast address is invalid')
        result['address'] = str(address)
        if data.get('gateway', '').strip():
            gateway = ipaddress.IPv4Address(data['gateway'].strip())
            if gateway not in address.network or gateway == address.ip:
                raise ValueError('网关应为同网段的另一地址 / Gateway must be another address on the same subnet')
            result['gateway'] = str(gateway)
        result['dns'] = ','.join(str(ipaddress.IPv4Address(x)) for x in
                                 data.get('dns', '').replace(',', ' ').split())
    return result


class Manager:
    def __init__(self):
        self.lock = threading.RLock()
        self.pending = None
        self.message = ''
        STATE.mkdir(parents=True, exist_ok=True)
        self.journal = STATE / 'pending.json'
        if self.journal.exists():
            self.pending = json.loads(self.journal.read_text())
            self.rollback()  # Recover interrupted or unconfirmed changes after host restart.

    def write_journal(self):
        tmp = self.journal.with_suffix('.tmp')
        with tmp.open('w') as f:
            json.dump(self.pending, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.journal)

    def status(self, include_devices=False):
        with self.lock:
            result = {'pending': copy.deepcopy(self.pending), 'message': self.message}
        if result['pending']:
            result['pending'].pop('previous', None)
            result['pending']['remaining_seconds'] = max(0, int(result['pending']['deadline'] - time.time()))
        if include_devices:
            result['interfaces'] = []
            for interface, kind in devices().items():
                key, active = profile(interface)
                values = settings(key) if key else {}
                result['interfaces'].append(dict(name=interface, type=kind, active=active,
                    method=values.get('ipv4.method', 'auto'), address=values.get('ipv4.addresses', ''),
                    gateway=values.get('ipv4.gateway', ''), dns=values.get('ipv4.dns', '')))
        return result

    def apply(self, data):
        data = validate_request(data)
        with self.lock:
            if self.pending:
                raise ValueError('请先确认或恢复上次更改 / Confirm or roll back the pending change')
            kind = devices().get(data['interface'])
            if not kind:
                raise ValueError('只能设置以太网或 Wi-Fi 网口 / Only Ethernet or Wi-Fi is supported')
            key, active = profile(data['interface'])
            if not key and kind == 'wifi':
                raise ValueError('请先通过 SSH 配置 Wi-Fi 连接 / Configure the Wi-Fi connection via SSH first')
            previous = settings(key) if key else None
            self.pending = dict(id=str(uuid.uuid4()), interface=data['interface'], uuid=key or str(uuid.uuid4()),
                previous=previous, was_active=active, request=data, status='applying', deadline=time.time()+120)
            self.write_journal()
            threading.Thread(target=self.activate, daemon=True).start()
            return self.status()

    def activate(self):
        time.sleep(2)  # Let the HTTP response reach the browser before re-addressing.
        with self.lock:
            p = self.pending
            if not p:
                return
            try:
                if p['previous'] is None:
                    nm('connection', 'add', 'type', 'ethernet', 'ifname', p['interface'],
                       'con-name', 'tsl-'+p['interface'], 'connection.uuid', p['uuid'], 'connection.autoconnect', 'no')
                r = p['request']
                nm('connection', 'modify', 'uuid', p['uuid'], 'ipv4.method', r['method'],
                   'ipv4.addresses', r['address'], 'ipv4.gateway', r['gateway'], 'ipv4.dns', r['dns'],
                   'ipv4.never-default', 'yes' if r['method'] == 'manual' and not r['gateway'] else 'no',
                   'connection.autoconnect', 'yes')
                nm('connection', 'up', 'uuid', p['uuid'], 'ifname', p['interface'])
                p['status'] = 'awaiting-confirmation'
                p['deadline'] = time.time()+120
                self.write_journal()
                self.message = '已应用，请在 120 秒内确认 / Applied; confirm within 120 seconds'
            except Exception as exc:
                self.message = '应用失败 / Apply failed: '+str(exc)
                self.rollback()

    def rollback(self):
        with self.lock:
            p = self.pending
            if not p:
                return
            try:
                if p['previous'] is not None:
                    args = [part for field, value in p['previous'].items() for part in (field, value)]
                    nm('connection', 'modify', 'uuid', p['uuid'], *args)
                    if p['was_active']:
                        nm('connection', 'up', 'uuid', p['uuid'], 'ifname', p['interface'])
                    else:
                        current = nm('-g', 'GENERAL.CON-UUID', 'device', 'show', p['interface'])
                        if current == p['uuid']:
                            nm('connection', 'down', 'uuid', p['uuid'])
                else:
                    existing = nm('-g', 'UUID', 'connection', 'show').splitlines()
                    if p['uuid'] in existing:
                        nm('connection', 'delete', 'uuid', p['uuid'])
                self.journal.rename(STATE / 'last-rollback.json')
                self.pending = None
                self.message += ' · 已恢复 / Rolled back'
            except Exception as exc:
                self.message = '恢复失败，将重试 / Rollback failed; retrying: '+str(exc)

    def confirm(self, change_id):
        with self.lock:
            if not self.pending or self.pending['id'] != change_id:
                raise ValueError('更改已过期 / Change expired')
            if self.pending['status'] != 'awaiting-confirmation' or time.time() >= self.pending['deadline']:
                raise ValueError('尚未生效或已超时 / Not applied or timed out')
            self.journal.rename(STATE / 'last-confirmed.json')
            self.pending = None
            self.message = '网口配置已持久保存 / Interface configuration saved persistently'

    def watch(self):
        while True:
            time.sleep(1)
            with self.lock:
                if self.pending and time.time() >= self.pending['deadline']:
                    self.rollback()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            self.respond(self.server.manager.status(self.path == '/interfaces'))
        except Exception as exc:
            self.respond({'error': str(exc)}, 400)

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 8192:
                raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(size))
            if self.path == '/apply':
                result = self.server.manager.apply(data)
            elif self.path == '/confirm':
                self.server.manager.confirm(data['id'])
                result = self.server.manager.status()
            elif self.path == '/rollback':
                self.server.manager.rollback()
                result = self.server.manager.status()
            else:
                raise ValueError('Unknown operation')
            self.respond(result)
        except Exception as exc:
            self.respond({'error': str(exc)}, 400)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


if __name__ == '__main__':
    manager = Manager()
    path = Path(SOCKET)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with Server(SOCKET, Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.manager = manager
        threading.Thread(target=manager.watch, daemon=True).start()
        server.serve_forever()
