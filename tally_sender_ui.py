#!/usr/bin/env python3
"""Standalone TSL UMD 5.0 UDP tester. Requires Python 3 with Tkinter."""
import ipaddress
import socket
import struct


def make_packet(index, color, channels=(True, True, True), screen=0):
    """channels: left, right, text; color: 0 off, 1 red, 2 green."""
    if not 0 <= index <= 10 or color not in (0, 1, 2):
        raise ValueError('Invalid ID or color')
    if not 0 <= screen <= 65535 or len(channels) != 3:
        raise ValueError('Invalid screen or channels')
    left, right, text = [color if enabled else 0 for enabled in channels]
    label = f'ID {index}'.encode('ascii')
    control = right | (text << 2) | (left << 4) | (3 << 6)
    return struct.pack('<HBBHHHH', 10 + len(label), 0, 0, screen,
                       index, control, len(label)) + label


def validate_target(ip, port):
    address = ipaddress.IPv4Address(ip.strip())
    if address.is_unspecified or address.is_multicast or str(address) == '255.255.255.255':
        raise ValueError('请输入单播 IPv4 地址 / Enter a unicast IPv4 address')
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError('UDP 端口范围 / UDP port range: 1–65535')
    return str(address), port


class SenderUI:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        self.root = root
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.active = {}
        self.count = 0
        self.ip = tk.StringVar(value='192.168.100.250')
        self.port = tk.StringVar(value='50080')
        self.screen = tk.StringVar(value='0')
        self.channels = [tk.BooleanVar(value=True) for _ in range(3)]
        self.status = tk.StringVar(value='就绪，尚未发送 / Ready, no packets sent')
        root.title('TSL 5.0 发送测试 / UDP Tally Sender')
        root.minsize(740, 690)
        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        config = ttk.LabelFrame(frame, text='目标设置 / Destination', padding=10)
        config.pack(fill='x')
        for row, (label, variable) in enumerate([
            ('目标 IP / Target IPv4', self.ip), ('UDP 端口 / Port', self.port),
            ('屏幕号 / Screen', self.screen),
        ]):
            ttk.Label(config, text=label).grid(row=row, column=0, sticky='w', pady=3)
            ttk.Entry(config, textvariable=variable, width=24).grid(row=row, column=1, sticky='ew')
        config.columnconfigure(1, weight=1)
        channel_frame = ttk.Frame(frame)
        channel_frame.pack(fill='x', pady=8)
        ttk.Label(channel_frame, text='灯位 / Channels:').pack(side='left')
        for label, var in zip(('左 / Left', '右 / Right', '文字 / Text'), self.channels):
            ttk.Checkbutton(channel_frame, text=label, variable=var).pack(side='left', padx=8)
        ttk.Label(frame, text='仅点击时发送，不自动重发。修改目标和灯位后，下次点击生效。\n'
                  'Send on click only, no repeating. Settings apply on the next click.\n'
                  '未勾选灯位发送灭灯 / Unchecked channels send OFF.').pack(anchor='w', pady=6)
        table = ttk.Frame(frame)
        table.pack(fill='both', expand=True)
        self.lamps = {}
        for index in range(11):
            ttk.Label(table, text=f'ID {index}', width=7).grid(row=index, column=0, padx=6, pady=3)
            lamp = tk.Label(table, text='未发送 / Idle', bg='#454545', fg='white', width=19)
            lamp.grid(row=index, column=1, padx=6)
            self.lamps[index] = lamp
            for column, (label, color) in enumerate((('红 / Red', 1), ('绿 / Green', 2), ('灭 / Off', 0)), 2):
                ttk.Button(table, text=label, command=lambda i=index, c=color: self.send({i: c})).grid(
                    row=index, column=column, padx=4, sticky='ew')
        for column in range(2, 5):
            table.columnconfigure(column, weight=1)
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=10)
        for label, color in (('全部红 / All red', 1), ('全部绿 / All green', 2), ('全部灭 / All off', 0)):
            ttk.Button(actions, text=label, command=lambda c=color: self.send(dict.fromkeys(range(11), c))).pack(side='left', padx=3)
        ttk.Label(frame, textvariable=self.status, wraplength=700).pack(anchor='w')
        ttk.Label(frame, text='显示最后一次发送的状态，不代表对端已收到；关闭窗口不发送灭灯。\n'
                  'Last sent state, not delivery confirmation. Closing does not send OFF.').pack(anchor='w', pady=(8, 0))
        root.protocol('WM_DELETE_WINDOW', self.close)

    def settings(self):
        target = validate_target(self.ip.get(), self.port.get())
        screen = int(self.screen.get())
        if not 0 <= screen <= 65535:
            raise ValueError('Screen: 0–65535')
        channels = tuple(v.get() for v in self.channels)
        if not any(channels):
            raise ValueError('至少选择一个灯位 / Select at least one channel')
        return target, screen, channels

    def send(self, changes):
        try:
            target, screen, channels = self.settings()
        except ValueError as error:
            self.status.set(f'设置错误 / Invalid settings: {error}')
            return
        try:
            for index, color in changes.items():
                self.sock.sendto(make_packet(index, color, channels, screen), target)
                self.count += 1
                self.active[index] = color
                self.lamps[index].configure(text=('灭 / Off', '红 / Red', '绿 / Green')[color],
                                           bg=('#454545', '#a82020', '#176c35')[color])
            self.status.set(f'UDP {target[0]}:{target[1]} · 已发 / Sent {self.count} · '
                            '单次发送 / One-shot')
        except OSError as error:
            self.status.set(f'发送失败 / TX failed: {error}')

    def close(self):
        self.sock.close()
        self.root.destroy()


if __name__ == '__main__':
    try:
        import tkinter as tk
    except ImportError:
        raise SystemExit('需要带 Tkinter 的 Python / Python with Tkinter is required.')
    window = tk.Tk()
    SenderUI(window)
    window.mainloop()
