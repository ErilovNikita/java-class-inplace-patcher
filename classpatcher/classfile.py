"""Small, bounds-checked reader for the parts of JVMS class files we need.

It deliberately never serializes a class file: offsets point into the original
byte sequence, which lets the caller patch only selected Code bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct


class ClassFormatError(ValueError):
    pass


class Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise ClassFormatError("truncated class file")
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def u1(self) -> int:
        return self.take(1)[0]

    def u2(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u4(self) -> int:
        return struct.unpack(">I", self.take(4))[0]


@dataclass(frozen=True)
class MethodCode:
    name: str
    descriptor: str
    code_offset: int
    code_length: int


@dataclass(frozen=True)
class ParsedClass:
    internal_name: str
    methods: tuple[MethodCode, ...]


def _utf8(pool: list[object | None], index: int) -> str:
    try:
        value = pool[index]
    except IndexError as exc:
        raise ClassFormatError(f"constant-pool index out of bounds: {index}") from exc
    if not isinstance(value, bytes):
        raise ClassFormatError(f"constant-pool index {index} is not UTF-8")
    # Attribute, class, method and descriptor names use the ASCII-compatible
    # subset. Decode only strings we actually need: unrelated constants may use
    # Java's Modified UTF-8 (for example an embedded NUL), which Python's UTF-8
    # codec intentionally rejects.
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClassFormatError(f"invalid UTF-8 in structural constant #{index}") from exc


def _skip_attributes(reader: Reader, pool: list[object | None]) -> None:
    for _ in range(reader.u2()):
        _utf8(pool, reader.u2())
        reader.take(reader.u4())


def _read_members(reader: Reader, pool: list[object | None], collect_methods: bool) -> list[MethodCode]:
    methods: list[MethodCode] = []
    for _ in range(reader.u2()):
        reader.u2()  # access_flags
        name = _utf8(pool, reader.u2())
        descriptor = _utf8(pool, reader.u2())
        for _ in range(reader.u2()):
            attribute_name = _utf8(pool, reader.u2())
            length = reader.u4()
            end = reader.pos + length
            if end > len(reader.data):
                raise ClassFormatError("truncated attribute")
            if collect_methods and attribute_name == "Code":
                # Code_attribute has max_stack, max_locals, code_length, code...
                reader.u2()
                reader.u2()
                code_length = reader.u4()
                code_offset = reader.pos
                reader.take(code_length)
                _skip_attributes_after_exception_table(reader, pool)
                if reader.pos != end:
                    raise ClassFormatError("invalid Code attribute length")
                methods.append(MethodCode(name, descriptor, code_offset, code_length))
            else:
                reader.take(length)
    return methods


def _skip_attributes_after_exception_table(reader: Reader, pool: list[object | None]) -> None:
    exception_count = reader.u2()
    reader.take(exception_count * 8)
    _skip_attributes(reader, pool)


def parse_class(data: bytes) -> ParsedClass:
    reader = Reader(data)
    if reader.take(4) != b"\xca\xfe\xba\xbe":
        raise ClassFormatError("not a Java class file (missing CAFEBABE)")
    reader.u2()  # minor
    reader.u2()  # major
    count = reader.u2()
    if count == 0:
        raise ClassFormatError("invalid constant_pool_count")
    pool: list[object | None] = [None] * count
    i = 1
    while i < count:
        tag = reader.u1()
        if tag == 1:  # CONSTANT_Utf8; names/descriptors are normally ASCII
            pool[i] = reader.take(reader.u2())
        elif tag in (3, 4):
            reader.take(4)
        elif tag in (5, 6):
            reader.take(8)
            i += 1  # long/double consumes the following pool slot
        elif tag in (7, 8, 16, 19, 20):
            # Every one of these entries has a u2 payload.  Only Class needs
            # its payload later, but String/MethodType/Module/Package must be
            # consumed too or the following high byte is mistaken for a tag.
            index = reader.u2()
            pool[i] = index if tag == 7 else None
        elif tag in (9, 10, 11, 12, 17, 18):
            reader.take(4)
        elif tag == 15:
            reader.take(3)
        else:
            raise ClassFormatError(
                f"unsupported or invalid constant-pool tag {tag} at pool index {i} "
                f"(class-file offset 0x{reader.pos - 1:X})"
            )
        i += 1
    reader.u2()  # access_flags
    this_class = reader.u2()
    reader.u2()  # super_class
    try:
        name_index = pool[this_class]
    except IndexError as exc:
        raise ClassFormatError("this_class index out of bounds") from exc
    if not isinstance(name_index, int):
        raise ClassFormatError("this_class is not a CONSTANT_Class")
    internal_name = _utf8(pool, name_index)
    reader.take(reader.u2() * 2)  # interfaces
    _read_members(reader, pool, collect_methods=False)  # fields
    methods = _read_members(reader, pool, collect_methods=True)
    _skip_attributes(reader, pool)  # class attributes
    if reader.pos != len(data):
        raise ClassFormatError("trailing or malformed class-file data")
    return ParsedClass(internal_name, tuple(methods))
