"""Absolute deadlines for the POSIX, main-thread participant and trusted CLIs."""
from contextlib import contextmanager
import signal
import threading
from .contracts import Pending


@contextmanager
def deadline(seconds):
    # Per-socket timeouts alone permit indefinite trickle responses. POSIX alarms
    # interrupt blocked reads, writes, TLS handshakes and header parsing as well.
    if threading.current_thread() is not threading.main_thread() or not 0 < seconds <= 120:
        raise ValueError("network operations require the bounded CLI thread")
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ValueError("overlapping deadline prohibited")
    def expired(*_):
        raise Pending("network deadline exceeded; retain the same run")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
