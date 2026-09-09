"""Windows TCP peer-account verification, before sending credentials.

The trust boundary is the Windows account, not individual processes within
that account (which can already read its configuration and IPC token).
"""
from __future__ import annotations

import ctypes
import os
import socket
from ctypes import wintypes


def _connection_owner(sock: socket.socket) -> int:
    if sock.family != socket.AF_INET:
        raise OSError("Unsupported IPC address family")
    # MIB_TCPROW_OWNER_PID / TCP_TABLE_OWNER_PID_ALL, from the Windows SDK.
    class Row(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in
                    ("state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]

    get_table = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    get_table.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                         wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    get_table.restype = wintypes.DWORD
    size = wintypes.DWORD()
    result = get_table(None, ctypes.byref(size), False, socket.AF_INET, 5, 0)
    if result not in (0, 122):
        raise OSError("Cannot inspect IPC connection owner")
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        result = get_table(buffer, ctypes.byref(size), False, socket.AF_INET, 5, 0)
        if result == 122:
            continue
        if result:
            raise OSError("Cannot inspect IPC connection owner")
        count = wintypes.DWORD.from_buffer(buffer).value
        offset = ctypes.sizeof(wintypes.DWORD)
        if offset + count * ctypes.sizeof(Row) > len(buffer):
            raise OSError("Invalid TCP owner table")
        local_ip, local_port = sock.getsockname()[:2]
        peer_ip, peer_port = sock.getpeername()[:2]
        matches = []
        for index in range(count):
            row = Row.from_buffer_copy(buffer, offset + index * ctypes.sizeof(Row))
            address = socket.inet_ntoa(int(row.local_addr).to_bytes(4, "little"))
            remote = socket.inet_ntoa(int(row.remote_addr).to_bytes(4, "little"))
            if (row.state == 5 and address == peer_ip and remote == local_ip
                    and socket.ntohs(row.local_port & 0xFFFF) == peer_port
                    and socket.ntohs(row.remote_port & 0xFFFF) == local_port):
                matches.append(int(row.pid))
        if len(matches) != 1 or matches[0] <= 0:
            raise OSError("IPC connection owner is ambiguous")
        return matches[0]
    raise OSError("TCP owner table changed repeatedly")


def _process_sid(pid: int) -> bytes:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.HANDLE)]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD)]
    security.GetTokenInformation.restype = wintypes.BOOL
    security.GetLengthSid.argtypes = [ctypes.c_void_p]
    security.GetLengthSid.restype = wintypes.DWORD
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        raise OSError("Cannot inspect IPC process")
    token = wintypes.HANDLE()
    try:
        if not security.OpenProcessToken(process, 8, ctypes.byref(token)):
            raise OSError("Cannot inspect IPC account")
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not 0 < size.value <= 65536:
            raise OSError("Invalid account token")
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise OSError("Cannot read IPC account")
        sid = ctypes.c_void_p.from_buffer(buffer).value
        length = security.GetLengthSid(sid)
        if not 0 < length <= 68:
            raise OSError("Invalid account SID")
        return ctypes.string_at(sid, length)
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


def verify_windows_peer(sock: socket.socket) -> None:
    if _process_sid(_connection_owner(sock)) != _process_sid(os.getpid()):
        raise OSError("IPC peer belongs to another Windows account")
