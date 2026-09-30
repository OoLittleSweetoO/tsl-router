import copy
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import shutil
import struct
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from protocol import decode, encode

ROOT = Path(__file__).parent
DEFAULT = dict(listen_port=40003, listen_interface='', output_ip='192.168.10.2', output_interface='eth0',
               forwarding=True, stale_seconds=5, rules=[])


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'数值须为 {low}–{high} 的整数 / Integer required: {low}–{high}')
    return value


def ipv4(value):
    return str(ipaddress.IPv4Address(value))


def validate_item(item):
    result = {k: integer(item[k], 0, 65535 if k in ('screen', 'id') else 3)
              for k in ('screen', 'id', 'left', 'right', 'text_color', 'brightness')}
    if not isinstance(item['text'], str):
        raise ValueError('标签必须为文字 / Label must be a string')
    result['text'] = item['text']
    encode(result)
    return result


def map_lamps(rule, item):
    out = dict(item)
    colors = dict(off=0, red=1, green=2, amber=3)
    for target, source in (('left', 'left_from'), ('right', 'right_from'), ('text_color', 'text_from')):
        out[target] = colors[rule[source]] if rule[source] in colors else item[rule[source]]
    return out


def validate(config):
    c = copy.deepcopy(config)
    c.setdefault('listen_interface', '')
    if not isinstance(c['listen_interface'], str) or len(c['listen_interface']) > 15:
        raise ValueError('接收网口名称无效 / Invalid input interface')
    integer(c['listen_port'], 1, 65535)
    c['output_ip'] = ipv4(c['output_ip'])
    if not isinstance(c['output_interface'], str) or len(c['output_interface']) > 15:
        raise ValueError('输出网口名称无效 / Invalid output interface')
    if type(c['forwarding']) is not bool:
        raise ValueError('转发开关无效 / Invalid forwarding switch')
    integer(c['stale_seconds'], 1, 3600)
    if not isinstance(c['rules'], list) or len(c['rules']) > 256:
        raise ValueError('最多 256 条规则 / Maximum 256 rules')
    ids, destinations = set(), set()
    for r in c['rules']:
        if not isinstance(r['key'], str) or not r['key'] or r['key'] in ids:
            raise ValueError('规则标识重复或为空 / Duplicate or empty rule key')
        ids.add(r['key'])
        if not isinstance(r['name'], str) or len(r['name']) > 100:
            raise ValueError('规则名称过长 / Rule name too long')
        if r['source_ip'] != '*':
            r['source_ip'] = ipv4(r['source_ip'])
        for k in ('source_screen', 'source_id'):
            if r[k] != '*':
                integer(r[k], 0, 65535)
        r['target_ip'] = ipv4(r['target_ip'])
        integer(r['target_port'], 1, 65535)
        for k in ('target_screen', 'target_id'):
            integer(r[k], 0, 65535)
        if r['mode'] not in ('auto', 'manual', 'disabled'):
            raise ValueError('映射模式无效 / Invalid rule mode')
        if r['label'] is not None and not isinstance(r['label'], str):
            raise ValueError('标签无效 / Invalid label')
        r['manual'] = validate_item(r['manual'])
        for k in ('left_from', 'right_from', 'text_from'):
            if r[k] not in ('left', 'right', 'text_color', 'off', 'red', 'green', 'amber'):
                raise ValueError('颜色映射无效 / Invalid lamp mapping')
        integer(r['repeat_ms'], 0, 60000)
        if 0 < r['repeat_ms'] < 100:
            raise ValueError('重发间隔至少 100ms，0 为不重发 / Repeat: at least 100ms; 0 disables repeat')
        dest = (r['target_ip'], r['target_port'], r['target_screen'], r['target_id'])
        if r['mode'] != 'disabled' and dest in destinations:
            raise ValueError('目标 IP / 端口 / Screen / ID 已占用 / Destination already assigned')
        if r['mode'] != 'disabled':
            destinations.add(dest)
        if r['label'] is not None:
            encode(dict(r['manual'], text=r['label']))
    return c


class Router:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.config = validate(json.loads(self.path.read_text()) if self.path.exists() else DEFAULT)
        self.inputs = {}
        self.outputs = {}
        self.events = deque(maxlen=100)
        self.rx = self.tx = self.errors = 0
        self.listener = None
        self.sender = None
        self.listener_error = ''
        self.output_error = ''
        self.running = True

    def event(self, message):
        self.events.appendleft(dict(time=time.time(), message=str(message)))

    def persist(self, config):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        with tmp.open('w') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix('.json.bak'))
        os.replace(tmp, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def new_listener(self, config):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if config['listen_interface']:
                if not hasattr(socket, 'SO_BINDTODEVICE'):
                    raise ValueError('绑定网口需要 Linux / Interface binding requires Linux')
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                             config['listen_interface'].encode() + b'\0')
            s.bind(('0.0.0.0', config['listen_port']))
            s.settimeout(0.5)
            return s
        except Exception:
            s.close()
            raise

    def setup_sender(self):
        if self.sender:
            self.sender.close()
        self.sender = None
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if self.config['output_interface']:
                iface = next((i for i in interfaces() if i['name'] == self.config['output_interface']), None)
                if not iface or not iface['ip']:
                    raise OSError(f"网口 {self.config['output_interface']} 无 IPv4 或未连接 / Disconnected or no IPv4; configure the interface first")
                self.config['output_ip'] = iface['ip']
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if self.config['output_interface'] and hasattr(socket, 'SO_BINDTODEVICE'):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                             self.config['output_interface'].encode() + b'\0')
            s.bind((self.config['output_ip'], 0))
            self.sender = s
            self.output_error = ''
        except OSError as exc:
            s.close()
            self.output_error = str(exc)

    def start(self):
        if not self.path.exists():
            self.persist(self.config)
        try:
            self.listener = self.new_listener(self.config)
        except (OSError, ValueError) as exc:
            self.listener_error = str(exc)
        self.setup_sender()
        threading.Thread(target=self.receive, daemon=True).start()
        threading.Thread(target=self.repeat, daemon=True).start()
        threading.Thread(target=self.refresh_network, daemon=True).start()

    def refresh_network(self):
        while self.running:
            time.sleep(2)
            with self.lock:
                iface = next((i for i in interfaces() if i['name'] == self.config['output_interface']), None)
                if iface and iface['ip'] and (not self.sender or iface['ip'] != self.config['output_ip']):
                    self.setup_sender()
                if not self.listener:
                    try:
                        self.listener = self.new_listener(self.config)
                        self.listener_error = ''
                    except (OSError, ValueError) as exc:
                        self.listener_error = str(exc)

    def save(self, config):
        c = validate(config)
        with self.lock:
            if c['output_interface']:
                iface = next((i for i in interfaces() if i['name'] == c['output_interface']), None)
                if not iface or not iface['ip']:
                    raise ValueError('发送网口无 IPv4，未保存 / Output interface unavailable or no IPv4; not saved')
                c['output_ip'] = iface['ip']
            listener_changed = any(c[k] != self.config[k] for k in ('listen_port', 'listen_interface'))
            # Interface-only changes can be applied to the existing bound socket.
            new = self.new_listener(c) if not self.listener or c['listen_port'] != self.config['listen_port'] else None
            if listener_changed and not new and self.listener:
                self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                                         c['listen_interface'].encode() + b'\0')
            try:
                self.persist(c)
            except Exception:
                if new:
                    new.close()
                elif listener_changed and self.listener:
                    self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                                             self.config['listen_interface'].encode() + b'\0')
                raise
            old_config = self.config
            self.config = c
            if new:
                old, self.listener = self.listener, new
                self.listener_error = ''
                if old:
                    old.close()
            unchanged = {r['key'] for r in c['rules'] if r in old_config['rules']}
            self.outputs = {k: v for k, v in self.outputs.items() if k in unchanged}
            if old_config['forwarding'] != c['forwarding']:
                self.outputs = {k: v for k, v in self.outputs.items() if v['mode'] == 'manual'}
            if any(old_config[k] != c[k] for k in ('output_ip', 'output_interface')):
                self.outputs.clear()
            self.setup_sender()
            self.event('配置已保存 / Configuration saved')

    def transmit(self, rule, item, mode):
        if not self.sender:
            self.setup_sender()
        if not self.sender:
            raise OSError(self.output_error)
        destination = (rule['target_ip'], rule['target_port'])
        if destination[1] == self.config['listen_port'] and destination[0] in (
                self.config['output_ip'], '127.0.0.1', '255.255.255.255'):
            raise ValueError('不能转发到自身 / Cannot forward to own listener')
        out = dict(item, screen=rule['target_screen'], id=rule['target_id'])
        self.sender.sendto(encode(out), destination)
        self.tx += 1
        self.output_error = ''
        self.outputs[rule['key']] = dict(item=out, time=time.time(), mode=mode,
                                        input_time=item.get('seen', time.time()))

    def safe_transmit(self, rule, item, mode):
        try:
            self.transmit(rule, item, mode)
        except (OSError, ValueError) as exc:
            self.errors += 1
            if self.output_error != str(exc):
                self.event(f'发送失败 / Send failed: {exc}')
            self.output_error = str(exc)

    def ingest(self, data, address):
        items = decode(data)  # Validate the whole datagram before applying any update.
        with self.lock:
            self.rx += 1
            for item in items:
                item.update(source_ip=address[0], source_port=address[1], seen=time.time())
                key = f"{address[0]}:{item['screen']}:{item['id']}"
                self.inputs.pop(key, None)
                self.inputs[key] = item
                if len(self.inputs) > 2048:
                    self.inputs.pop(next(iter(self.inputs)))
                if not self.config['forwarding']:
                    continue
                for r in self.config['rules']:
                    if r['mode'] != 'auto' or r['source_ip'] not in ('*', address[0]):
                        continue
                    if not all(r[k] == '*' or item[field] == 65535 or r[k] == item[field]
                               for k, field in (('source_screen', 'screen'), ('source_id', 'id'))):
                        continue
                    out = map_lamps(r, item)
                    if r['label'] is not None:
                        out['text'] = r['label']
                    self.safe_transmit(r, out, 'auto')

    def receive(self):
        while self.running:
            sock = self.listener
            if sock is None:
                time.sleep(0.2)
                continue
            try:
                data, address = sock.recvfrom(65535)
                # Ignore our own output socket if a broadcast comes back.
                with self.lock:
                    if sock is not self.listener:
                        continue
                    if self.sender and address == self.sender.getsockname():
                        continue
                    self.ingest(data, address)
            except socket.timeout:
                pass
            except (ValueError, UnicodeError) as exc:
                with self.lock:
                    self.errors += 1
                    if not self.events or self.events[0]['message'] != f'忽略非法报文 / Invalid packet ignored: {exc}':
                        self.event(f'忽略非法报文 / Invalid packet ignored: {exc}')
            except OSError as exc:
                if sock is self.listener:
                    self.listener_error = str(exc)
                    time.sleep(0.2)

    def repeat(self):
        while self.running:
            time.sleep(0.05)
            with self.lock:
                now = time.time()
                for r in self.config['rules']:
                    out = self.outputs.get(r['key'])
                    if not out or r['mode'] == 'disabled' or not r['repeat_ms']:
                        continue
                    # Input age affects monitoring only; keep the last state on the wire.
                    if r['mode'] == 'auto' and not self.config['forwarding']:
                        continue
                    if now - out['time'] >= r['repeat_ms'] / 1000:
                        self.safe_transmit(r, out['item'], r['mode'])

    def action(self, data):
        with self.lock:
            key = data['key']
            r = next((r for r in self.config['rules'] if r['key'] == key), None)
            if r is None:
                raise ValueError('找不到规则 / Rule not found')
            mode = data['mode']
            if mode not in ('manual', 'auto', 'disabled'):
                raise ValueError('模式无效 / Invalid mode')
            item = validate_item(data['item']) if mode == 'manual' else r['manual']
            c = copy.deepcopy(self.config)
            updated = next(x for x in c['rules'] if x['key'] == key)
            updated.update(mode=mode, manual=item)
            c = validate(c)
            self.persist(c)
            self.config = c
            self.outputs.pop(key, None)
            if mode == 'manual':
                out = map_lamps(updated, item)
                if data.get('blackout') is True:
                    out.update(left=0, right=0, text_color=0)
                self.transmit(updated, out, mode)
            self.event(f"{r['name']} → {mode}")

    def snapshot(self):
        with self.lock:
            return dict(config=copy.deepcopy(self.config), inputs=list(self.inputs.values()),
                        outputs=copy.deepcopy(self.outputs), events=list(self.events),
                        stats=dict(rx=self.rx, tx=self.tx, errors=self.errors), now=time.time(),
                        listener_error=self.listener_error, output_error=self.output_error,
                        persistence=dict(path=str(self.path), saved=self.path.exists()),
                        interfaces=interfaces())


def interfaces():
    result = []
    for _, name in socket.if_nameindex():
        if name == 'lo' or name.startswith(('veth', 'docker', 'br-')):
            continue
        address = ''
        try:
            import fcntl
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                raw = fcntl.ioctl(s.fileno(), 0x8915, struct.pack('256s', name.encode()[:15]))
                address = socket.inet_ntoa(raw[20:24])
        except (ImportError, OSError):
            pass
        sys_path = Path('/sys/class/net') / name
        kind = 'Wi-Fi' if (sys_path / 'wireless').exists() else ('USB 网卡 / USB Ethernet' if '/usb' in str((sys_path / 'device').resolve()) else '有线网卡 / Ethernet')
        try:
            status = (sys_path / 'operstate').read_text().strip()
        except OSError:
            status = 'unknown'
        result.append(dict(name=name, ip=address, kind=kind, status=status))
    return result


def network_request(path, data=None):
    conn = http.client.HTTPConnection('localhost', timeout=60)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(60)
    try:
        sock.connect(os.environ.get('NETWORK_SOCKET', '/run/tsl-network/control.sock'))
        conn.sock = sock
        conn.request('POST' if data is not None else 'GET', path,
                     body=json.dumps(data) if data is not None else None,
                     headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        result = json.loads(response.read())
        if response.status != 200:
            raise ValueError(result.get('error', 'Network configuration failed'))
        return result
    finally:
        conn.close()
        sock.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, code=200, mime='application/json; charset=utf-8'):
        body = json.dumps(value, ensure_ascii=False).encode() if mime.startswith('application/json') else value
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/state':
            self.reply(self.server.router.snapshot())
        elif path == '/health':
            self.reply(dict(ok=True))
        elif path in ('/api/network/interfaces', '/api/network/status'):
            try:
                self.reply(network_request('/'+path.rsplit('/', 1)[1]))
            except (OSError, ValueError) as exc:
                self.reply(dict(error='网络助手不可用 / Network helper unavailable: '+str(exc)), 503)
        elif path in ('/', '/app.js', '/style.css'):
            name, mime = {'/': ('index.html', 'text/html; charset=utf-8'),
                          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                          '/style.css': ('style.css', 'text/css; charset=utf-8')}[path]
            self.reply((ROOT / 'static' / name).read_bytes(), mime=mime)
        else:
            self.reply(dict(error='Not found'), 404)

    def do_POST(self):
        try:
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                self.reply(dict(error='不允许跨站请求 / Cross-origin request denied'), 403)
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('仅接受 JSON / JSON required')
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 1048576:
                raise ValueError('请求大小无效 / Invalid request size')
            data = json.loads(self.rfile.read(length))
            if self.path == '/api/config':
                self.server.router.save(data)
            elif self.path == '/api/action':
                self.server.router.action(data)
            elif self.path in ('/api/network/apply', '/api/network/confirm', '/api/network/rollback'):
                self.reply(network_request('/'+self.path.rsplit('/', 1)[1], data))
                return
            else:
                self.reply(dict(error='Not found'), 404)
                return
            self.reply(dict(ok=True))
        except (ValueError, KeyError, TypeError, OSError, StopIteration) as exc:
            self.reply(dict(error=str(exc)), 400)


if __name__ == '__main__':
    router = Router(os.environ.get('CONFIG_PATH', '/data/config.json'))
    router.start()
    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('WEB_PORT', '8080'))), Handler)
    server.router = router
    print('TSL Router ready', flush=True)
    server.serve_forever()
