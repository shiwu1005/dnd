"""API Key 的本机保存与读取。

Windows 下使用 DPAPI（CryptProtectData）加密，密文只能由当前 Windows
用户解密；其他平台退化为仅本用户可读的明文文件。

文件位置：应用目录下的 key.dat（与 exe / game.db 同级）。
"""
import base64
import os
import sys

from . import config

KEY_FILENAME = "key.dat"


def key_path():
    return os.path.join(config.APP_DIR, KEY_FILENAME)


# ---------- Windows DPAPI ----------
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()

    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(
        ctypes.byref(blob_in),
        None, None, None, None,
        0,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError("DPAPI 调用失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _is_windows():
    return sys.platform.startswith("win")


# ---------- 对外接口 ----------
def save_key(api_key: str) -> bool:
    """保存 API Key。成功返回 True。"""
    if not api_key:
        return False
    try:
        raw = api_key.encode("utf-8")
        payload = _dpapi(raw, protect=True) if _is_windows() else raw
        data = b"DPAPI:" + base64.b64encode(payload)
        path = key_path()
        with open(path, "wb") as f:
            f.write(data)
        # 非 Windows 收紧权限
        if not _is_windows():
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return True
    except Exception:  # noqa: BLE001
        return False


def load_key() -> str:
    """读取已保存的 API Key；不存在或解密失败返回空串。"""
    path = key_path()
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return ""
    try:
        if data.startswith(b"DPAPI:"):
            payload = base64.b64decode(data[len(b"DPAPI:"):])
            raw = _dpapi(payload, protect=False)
        else:
            raw = data
        return raw.decode("utf-8").strip()
    except Exception:  # noqa: BLE001
        return ""


def forget_key() -> bool:
    """删除已保存的 API Key。返回是否确实删除了文件。"""
    try:
        os.remove(key_path())
        return True
    except OSError:
        return False


def has_saved_key() -> bool:
    return os.path.exists(key_path())