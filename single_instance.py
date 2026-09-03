"""Utilities for preventing more than one File Lister process per session."""

import sys
from typing import Optional


# A named mutex is owned by the operating system, so it is released even when
# the application is terminated without running its normal shutdown code.
_MUTEX_NAME = "Local\\FileListerDatabaseManager_SingleInstance"
_ERROR_ALREADY_EXISTS = 183
_mutex_handle: Optional[int] = None


def acquire() -> bool:
    """Reserve the application's single-instance mutex.

    Returns ``False`` when another File Lister process is already running.
    The mutex handle is deliberately retained for the lifetime of this
    process.
    """
    global _mutex_handle

    if _mutex_handle is not None:
        return True

    # The distributed application targets Windows.  Keep source launches on
    # other platforms functional rather than preventing every such launch.
    if sys.platform != "win32":
        _mutex_handle = 1
        return True

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    create_mutex.restype = ctypes.c_void_p

    handle = create_mutex(None, False, _MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False

    _mutex_handle = handle
    return True
