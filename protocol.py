"""TSL UMD 5.0 display messages (UDP, little endian)."""
import struct


def decode(data):
    if not 6 <= len(data) <= 2048:
        raise ValueError('报文长度须为 6–2048 字节 / Packet size must be 6–2048 bytes')
    size, version, flags, screen = struct.unpack_from('<HBBH', data)
    if size != len(data) - 2 or version != 0 or flags & ~1:
        raise ValueError('长度、版本或类型不支持 / Unsupported length, version or screen control')
    items, offset = [], 6
    while offset < len(data):
        if offset + 6 > len(data):
            raise ValueError('Display 头部不完整 / Truncated display header')
        index, control, length = struct.unpack_from('<HHH', data, offset)
        if control & 0x8000:
            raise ValueError('不支持 Display 控制数据 / Unsupported display control data')
        offset += 6
        if offset + length > len(data):
            raise ValueError('文字长度超出报文 / Text exceeds packet length')
        text = data[offset:offset + length].decode('utf-16-le' if flags & 1 else 'ascii')
        items.append(dict(screen=screen, id=index, left=(control >> 4) & 3,
                          right=control & 3, text_color=(control >> 2) & 3,
                          brightness=(control >> 6) & 3, text=text))
        offset += length
    return items


def encode(item):
    unicode = not item['text'].isascii()
    text = item['text'].encode('utf-16-le' if unicode else 'ascii')
    if len(text) > 2036:
        raise ValueError('文字最多 2036 字节 / Encoded text limit: 2036 bytes')
    control = item['right'] | item['text_color'] << 2 | item['left'] << 4 | item['brightness'] << 6
    return struct.pack('<HBBHHHH', 10 + len(text), 0, int(unicode), item['screen'],
                       item['id'], control, len(text)) + text
