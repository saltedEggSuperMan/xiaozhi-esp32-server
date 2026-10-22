"""Read-only Windows Core Audio discovery. No default-device/volume setters.

COM objects are created, used and released on the calling thread. Only render
endpoints are enumerated; pairing records and capture devices are never used.
"""
import ctypes as C
import os
import re
import uuid
from contextlib import contextmanager


class GUID(C.Structure):
    _fields_ = [('data', C.c_ubyte * 16)]

    def __init__(self, value):
        super().__init__()
        self.data[:] = uuid.UUID(value).bytes_le


class PropertyKey(C.Structure):
    _fields_ = [('fmtid', GUID), ('pid', C.c_uint32)]


class PropVariant(C.Structure):
    _fields_ = [('vt', C.c_uint16), ('reserved', C.c_uint16 * 3),
                ('value', C.c_void_p), ('extra', C.c_void_p)]


def call(obj, slot, args=(), types=()):
    table = C.cast(obj, C.POINTER(C.POINTER(C.c_void_p))).contents
    result = C.WINFUNCTYPE(C.c_long, C.c_void_p, *types)(table[slot])(obj, *args)
    if result < 0:
        raise RuntimeError(f'Windows 音频接口失败: 0x{result & 0xffffffff:08x}')
    return result


def release(obj):
    if obj:
        call(obj, 2)


@contextmanager
def enumerator():
    if os.name != 'nt':
        raise RuntimeError('指定 PC 设备需要在交互用户的 Windows 桌面运行；WSL/Docker/远程主机请在 Windows 运行本地播放服务')
    session = C.c_uint32()
    if not C.WinDLL('kernel32').ProcessIdToSessionId(os.getpid(), C.byref(session)) or session.value == 0:
        raise RuntimeError('音频播放需要已登录的 Windows 交互用户；不支持 Session 0 服务，请使用用户登录后启动')
    ole = C.WinDLL('ole32')
    result = ole.CoInitializeEx(None, 0)
    initialized = result >= 0
    if result < 0 and result & 0xffffffff != 0x80010106:
        raise RuntimeError('无法初始化 Windows 音频接口')
    obj = C.c_void_p()
    try:
        clsid = GUID('bcde0395-e52f-467c-8e3d-c4579291692e')
        iid = GUID('a95664d2-9614-4f35-a746-de8db63617e6')
        hr = ole.CoCreateInstance(C.byref(clsid), None, 1, C.byref(iid), C.byref(obj))
        if hr < 0:
            raise RuntimeError('无法访问 Windows 音频设备枚举器')
        yield obj
    finally:
        release(obj)
        if initialized:
            ole.CoUninitialize()


def endpoint_id(device):
    value = C.c_void_p()
    call(device, 5, (C.byref(value),), (C.c_void_p,))
    try:
        return C.wstring_at(value)
    finally:
        C.OleDLL('ole32').CoTaskMemFree(value)


def friendly_name(device):
    store = C.c_void_p()
    call(device, 4, (0, C.byref(store)), (C.c_uint32, C.c_void_p))
    value = PropVariant()
    try:
        key = PropertyKey(GUID('a45c254e-df1c-4efd-8020-67d146a850e0'), 14)
        call(store, 5, (C.byref(key), C.byref(value)), (C.c_void_p, C.c_void_p))
        return C.wstring_at(value.value) if value.vt == 31 and value.value else ''
    finally:
        C.OleDLL('ole32').PropVariantClear(C.byref(value))
        release(store)


def mpv_device(device_id):
    # Accept only a full Windows render endpoint ID, never a name, index or auto.
    if not isinstance(device_id, str) or not re.fullmatch(
            r'\{0\.0\.0\.00000000\}\.\{[0-9a-fA-F-]{36}\}', device_id):
        raise ValueError('PC_DEVICE 必须选择有效的 Windows 输出终端 ID')
    uuid.UUID(device_id.rsplit('{', 1)[1].rstrip('}'))
    return 'wasapi/' + device_id


def list_outputs(selected=None):
    devices = []
    with enumerator() as enum:
        collection = C.c_void_p()
        call(enum, 3, (0, 15, C.byref(collection)), (C.c_int, C.c_uint32, C.c_void_p))
        try:
            count = C.c_uint32()
            call(collection, 3, (C.byref(count),), (C.c_void_p,))
            for index in range(count.value):
                device = C.c_void_p()
                call(collection, 4, (index, C.byref(device)), (C.c_uint32, C.c_void_p))
                try:
                    state = C.c_uint32()
                    call(device, 6, (C.byref(state),), (C.c_void_p,))
                    ident = endpoint_id(device)
                    try:
                        name = friendly_name(device)
                    except (RuntimeError, OSError):
                        # Removed endpoints can retain an ID but lose properties.
                        name = ident
                    devices.append(dict(id=ident, name=name, direction='output',
                                        state={1:'active', 2:'disabled', 4:'not_present', 8:'unplugged'}.get(state.value, 'unknown'),
                                        available=state.value == 1, selected=ident == selected,
                                        hands_free=bool(re.search(r'Hands.Free|免提|AG Audio', name, re.I))))
                finally:
                    release(device)
        finally:
            release(collection)
    return devices


def require_output(ident):
    mpv_device(ident)
    device = next((d for d in list_outputs(ident) if d['selected']), None)
    if not device or not device['available']:
        raise RuntimeError('选定音频设备未连接或不可用；已停止输出，不回退默认设备。请连接原设备或重新选择')
    return device


def system_snapshot():
    """Read defaults for all roles and endpoint master volume for verification."""
    result = {}
    with enumerator() as enum:
        for role, label in enumerate(('console', 'multimedia', 'communications')):
            device, volume = C.c_void_p(), C.c_void_p()
            try:
                call(enum, 4, (0, role, C.byref(device)), (C.c_int, C.c_int, C.c_void_p))
                iid = GUID('5cdf2c82-841e-4546-9722-0cf74078229a')
                call(device, 3, (C.byref(iid), 23, None, C.byref(volume)),
                     (C.c_void_p, C.c_uint32, C.c_void_p, C.c_void_p))
                level = C.c_float()
                call(volume, 9, (C.byref(level),), (C.c_void_p,))
                result[label] = dict(id=endpoint_id(device), volume=level.value)
            finally:
                release(volume)
                release(device)
    return result
