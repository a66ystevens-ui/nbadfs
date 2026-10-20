"""Process-local credential override for the single-user local dashboard."""
import os
from threading import Lock
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import SecretStr

_lock = Lock()
_sportsdataio: SecretStr | None = None
_scoped_key = ContextVar('courtvision_scoped_key',default=())


@contextmanager
def session_credentials(value):
    """Hosted sessions never mutate process-wide keys or inherit another user's key."""
    token = _scoped_key.set((value,))
    try:
        yield
    finally:
        _scoped_key.reset(token)


def sportsdataio_key():
    scoped = _scoped_key.get()
    if scoped:
        return scoped[0]
    with _lock:
        return _sportsdataio.get_secret_value() if _sportsdataio is not None else os.environ.get('SPORTSDATAIO_API_KEY')


def set_sportsdataio_key(value):
    global _sportsdataio
    with _lock:
        _sportsdataio = SecretStr(value) if value is not None else None


def status():
    with _lock:
        return {'key_present':bool(_sportsdataio or os.environ.get('SPORTSDATAIO_API_KEY')),
                'key_source':'session' if _sportsdataio is not None else
                             ('environment' if os.environ.get('SPORTSDATAIO_API_KEY') else None)}
