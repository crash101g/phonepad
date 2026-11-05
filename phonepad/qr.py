"""A small QR code encoder: byte mode, error correction level L, versions 1 to 5 (up to 106 bytes).

That covers the pairing link, and it means this application needs no QR library.
"""

# version -> (data codewords, error correction codewords). All are a single block.
VERSIONS = {1: (19, 7), 2: (34, 10), 3: (55, 15), 4: (80, 20), 5: (108, 26)}
MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]

# Galois field GF(256) with the QR polynomial, as log tables.
EXP = [0] * 510
LOG = [0] * 256
_value = 1
for _i in range(255):
    EXP[_i] = EXP[_i + 255] = _value
    LOG[_value] = _i
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11d


def _mul(a, b):
    return EXP[LOG[a] + LOG[b]] if a and b else 0


def reed_solomon(data, count):
    """[count] error correction codewords for [data]."""
    generator = [1]
    for i in range(count):
        generator = [a ^ _mul(b, EXP[i]) for a, b in zip(generator + [0], [0] + generator)]
    remainder = [0] * count
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for i in range(count):
            remainder[i] ^= _mul(generator[i + 1], factor)
    return remainder


def _codewords(data, version):
    capacity, ec_count = VERSIONS[version]
    bits = '0100' + format(len(data), '08b') + ''.join(format(b, '08b') for b in data)
    bits = bits + '0' * min(4, capacity * 8 - len(bits))
    bits += '0' * (-len(bits) % 8)
    words = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = 0xec
    while len(words) < capacity:
        words.append(pad)
        pad ^= 0xec ^ 0x11
    return words + reed_solomon(words, ec_count)


def _base(version):
    """The fixed patterns: returns (modules, reserved)."""
    size = 17 + 4 * version
    modules = [[False] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def put(x, y, dark):
        modules[y][x] = dark
        reserved[y][x] = True

    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    ring = max(abs(dx), abs(dy))
                    put(x, y, ring != 2 and ring != 4)
    for i in range(8, size - 8):
        put(i, 6, i % 2 == 0)
        put(6, i, i % 2 == 0)
    if version > 1:
        centre = size - 7
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                put(centre + dx, centre + dy, max(abs(dx), abs(dy)) != 1)
    for i in range(9):  # room for the format bits; _format() fills them in
        reserved[8][i] = reserved[i][8] = True
    for i in range(8):
        reserved[8][size - 1 - i] = reserved[size - 1 - i][8] = True
    return modules, reserved


def _format(modules, mask):
    size = len(modules)
    data = 1 << 3 | mask  # 01 is level L
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = (data << 10 | rem) ^ 0x5412
    bit = lambda i: (bits >> i) & 1 == 1
    for i in range(6):
        modules[i][8] = bit(i)
    modules[7][8] = bit(6)
    modules[8][8] = bit(7)
    modules[8][7] = bit(8)
    for i in range(9, 15):
        modules[8][14 - i] = bit(i)
    for i in range(8):
        modules[8][size - 1 - i] = bit(i)
    for i in range(8, 15):
        modules[size - 15 + i][8] = bit(i)
    modules[size - 8][8] = True


def _place(modules, reserved, words, mask):
    size = len(modules)
    out = [row[:] for row in modules]
    stream = [(w >> (7 - i)) & 1 == 1 for w in words for i in range(8)]
    at = 0
    right = size - 1
    upward = True
    while right > 0:
        if right == 6:
            right -= 1  # the vertical timing line
        for y in (range(size - 1, -1, -1) if upward else range(size)):
            for x in (right, right - 1):
                if not reserved[y][x]:
                    dark = stream[at] if at < len(stream) else False
                    at += 1
                    out[y][x] = dark != MASKS[mask](x, y)
        upward = not upward
        right -= 2
    _format(out, mask)
    return out


def _penalty(m):
    """The standard's penalty score, which picks the mask that scanners read most easily."""
    size = len(m)
    score = 0
    columns = [list(c) for c in zip(*m)]
    for line in m + columns:
        run = 1
        for a, b in zip(line, line[1:]):
            run = run + 1 if a == b else 1
            if run == 5:
                score += 3
            elif run > 5:
                score += 1
        text = ''.join('1' if v else '0' for v in line)
        score += 40 * (text.count('10111010000') + text.count('00001011101'))
    for y in range(size - 1):
        for x in range(size - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, m))
    score += abs(dark * 20 // (size * size) - 10) * 10
    return score


def encode(text):
    """The QR code for [text], as rows of booleans (True is dark). Raises ValueError if it is too long."""
    data = text.encode('utf-8')
    for version, (capacity, _) in VERSIONS.items():
        if len(data) <= capacity - 2:
            break
    else:
        raise ValueError('Text too long for a QR code here')
    modules, reserved = _base(version)
    words = _codewords(data, version)
    return min((_place(modules, reserved, words, mask) for mask in range(8)), key=_penalty)


def to_terminal(matrix):
    """The code as text, two rows to a character, with the quiet zone scanners need."""
    border = 2
    size = len(matrix) + border * 2
    dark = lambda x, y: border <= x < size - border and border <= y < size - border and matrix[y - border][x - border]
    glyph = {(0, 0): '█', (0, 1): '▀', (1, 0): '▄', (1, 1): ' '}
    return '\n'.join(''.join(glyph[dark(x, y), dark(x, y + 1)] for x in range(size)) for y in range(0, size, 2))


def to_rgba(matrix, scale=8, border=4):
    """The code as RGBA pixels: returns (bytes, width in pixels). Height equals width."""
    light = b'\xff\xff\xff\xff'
    black = b'\x00\x00\x00\xff'
    width = (len(matrix) + border * 2) * scale
    blank = light * width
    rows = [blank] * (border * scale)
    for row in matrix:
        pixels = light * (border * scale) + b''.join((black if v else light) * scale for v in row) + light * (border * scale)
        rows += [pixels] * scale
    rows += [blank] * (border * scale)
    return b''.join(rows), width
