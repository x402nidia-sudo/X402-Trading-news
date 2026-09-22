"""Local dedicated-agent wallet: DPAPI on Windows, private file permissions on Unix."""
import base64
import json
import os
from algosdk import account,mnemonic
from nacl.signing import SigningKey
from .config import private_write


def parse_secret(value):
    value=value.strip()
    if len(value.split())==25:key=mnemonic.to_private_key(value)
    else:
        raw=base64.b64decode(value,validate=True)
        if len(raw)!=64:raise ValueError('Enter the 25-word recovery phrase or a valid Algorand private key.')
        key=value
    raw=base64.b64decode(key,validate=True)
    if bytes(SigningKey(raw[:32]).verify_key)!=raw[32:]:
        raise ValueError('The key does not match a valid Algorand wallet.')
    return key,account.address_from_private_key(key)


def dpapi(data,decrypt=False):
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_byte))]
    buf=ctypes.create_string_buffer(data);source=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_byte)));out=Blob()
    fn=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)):raise OSError('The key could not be protected for your Windows user.')
    try:return ctypes.string_at(out.pbData,out.cbData)
    finally:ctypes.windll.kernel32.LocalFree(out.pbData)


def save_secret(path,key):
    if os.name=='nt':body={'format':'windows-dpapi','data':base64.b64encode(dpapi(key.encode())).decode()}
    else:body={'format':'unix-private-file','data':key}
    private_write(path,json.dumps(body))


def load_secret(path):
    body=json.loads(path.read_text())
    if body['format']=='windows-dpapi':
        if os.name!='nt':raise ValueError('This key belongs to your Windows user. Set up automatic mode again on this computer.')
        key=dpapi(base64.b64decode(body['data']),True).decode()
    elif body['format']=='unix-private-file':
        if os.name!='nt' and path.stat().st_mode & 0o077:raise ValueError('The key file permissions are too broad. Run the installer again.')
        key=body['data']
    else:raise ValueError('Invalid wallet file.')
    return parse_secret(key)
