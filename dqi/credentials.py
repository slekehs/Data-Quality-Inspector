"""The Inspector's single local generic-password item in macOS Keychain."""
import ctypes as c
from contextlib import ExitStack
from functools import lru_cache
import sys

from .semantic_provider import valid_api_key


_NOT_FOUND = -25300
_DUPLICATE = -25299


class CredentialError(ValueError):
    """A safe, user-facing error. Never incorporate Keychain data or input."""


@lru_cache(maxsize=1)
def _frameworks():
    if sys.platform != "darwin":
        raise CredentialError("Saving an API key requires macOS Keychain. You can enter a key for this review.")
    try:
        cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        sec = c.CDLL("/System/Library/Frameworks/Security.framework/Security")
        ptr = c.c_void_p
        for lib, name, result, args in (
            (cf, "CFRelease", None, [ptr]),
            (cf, "CFStringCreateWithCString", ptr, [ptr, c.c_char_p, c.c_uint32]),
            (cf, "CFDataCreate", ptr, [ptr, c.c_char_p, c.c_long]),
            (cf, "CFDataGetLength", c.c_long, [ptr]),
            (cf, "CFDataGetBytePtr", ptr, [ptr]),
            (cf, "CFDictionaryCreate", ptr, [ptr, c.POINTER(ptr), c.POINTER(ptr), c.c_long, ptr, ptr]),
            (sec, "SecItemCopyMatching", c.c_int32, [ptr, c.POINTER(ptr)]),
            (sec, "SecItemAdd", c.c_int32, [ptr, c.POINTER(ptr)]),
            (sec, "SecItemUpdate", c.c_int32, [ptr, ptr]),
            (sec, "SecItemDelete", c.c_int32, [ptr]),
        ):
            function = getattr(lib, name)
            function.restype, function.argtypes = result, args
        return cf, sec
    except (OSError, AttributeError):
        raise CredentialError("macOS Keychain is unavailable. You can enter a key for this review.") from None


def _check(status):
    if status == _NOT_FOUND:
        raise CredentialError("No saved API key was found. Save one in Settings or enter a key for this review.")
    if status in {-128, -25293, -25308}:
        raise CredentialError("Keychain access was cancelled or unavailable. Unlock your keychain or enter a key for this review.")
    if status:
        raise CredentialError("macOS Keychain could not complete the request. Check your keychain and try again.")


class _Request(ExitStack):
    """Own each created/copied Core Foundation object for one SecItem operation."""

    def __init__(self):
        super().__init__()
        self.cf, self.sec = _frameworks()

    def own(self, pointer):
        if not pointer:
            raise CredentialError("macOS Keychain could not prepare this request.")
        self.callback(self.cf.CFRelease, pointer)
        return pointer

    def constant(self, name):
        return c.c_void_p.in_dll(self.sec, name).value

    def dictionary(self, values):
        pointers = c.c_void_p * len(values)
        keys = pointers(*(self.constant(name) for name in values))
        key_callbacks = c.addressof(c.c_byte.in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks"))
        value_callbacks = c.addressof(c.c_byte.in_dll(self.cf, "kCFTypeDictionaryValueCallBacks"))
        return self.own(self.cf.CFDictionaryCreate(None, keys, pointers(*values.values()), len(values), key_callbacks, value_callbacks))

    def identity(self):
        utf8 = 0x08000100  # kCFStringEncodingUTF8
        return {
            "kSecClass": self.constant("kSecClassGenericPassword"),
            "kSecAttrService": self.own(self.cf.CFStringCreateWithCString(None, b"Data Quality Inspector", utf8)),
            "kSecAttrAccount": self.own(self.cf.CFStringCreateWithCString(None, b"OpenAI API key", utf8)),
        }

    def copy(self, secret):
        query = self.identity()
        query["kSecReturnData" if secret else "kSecReturnAttributes"] = c.c_void_p.in_dll(self.cf, "kCFBooleanTrue").value
        if not secret:
            # A status check at page load must never prompt to unlock an item.
            query["kSecUseAuthenticationUI"] = self.constant("kSecUseAuthenticationUIFail")
        result = c.c_void_p()
        status = self.sec.SecItemCopyMatching(self.dictionary(query), c.byref(result))
        if status == _NOT_FOUND and not secret:
            return False
        _check(status)
        self.own(result.value)
        if not secret:
            return True
        size = self.cf.CFDataGetLength(result)
        if not 16 <= size <= 4096:
            raise CredentialError("The saved key is invalid. Replace it in Settings.")
        try:
            key = c.string_at(self.cf.CFDataGetBytePtr(result), size).decode("ascii")
        except UnicodeDecodeError:
            raise CredentialError("The saved key is invalid. Replace it in Settings.") from None
        if not valid_api_key(key):
            raise CredentialError("The saved key is invalid. Replace it in Settings.")
        return key


def key_status():
    if sys.platform != "darwin":
        return {"supported": False, "saved": False}
    with _Request() as request:
        return {"supported": True, "saved": request.copy(secret=False)}


def load_key():
    with _Request() as request:
        return request.copy(secret=True)


def save_key(key):
    if not valid_api_key(key):
        raise CredentialError("Enter a valid API key without spaces or control characters.")
    with _Request() as request:
        raw = key.encode("ascii")
        data = request.own(request.cf.CFDataCreate(None, raw, len(raw)))
        identity = request.identity()
        value = {"kSecValueData": data}
        status = request.sec.SecItemAdd(request.dictionary(identity | value), None)
        if status == _DUPLICATE:
            status = request.sec.SecItemUpdate(request.dictionary(identity), request.dictionary(value))
        _check(status)
    return {"supported": True, "saved": True}


def remove_key():
    with _Request() as request:
        status = request.sec.SecItemDelete(request.dictionary(request.identity()))
        if status != _NOT_FOUND:
            _check(status)
    return {"supported": True, "saved": False}
