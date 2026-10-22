"""Content-free operational measurements; never log payloads or exceptions."""
from contextlib import contextmanager
import logging
from time import perf_counter

logger = logging.getLogger('dart_insight.operations')


@contextmanager
def measure(stage, **counts):
    start = perf_counter()
    outcome = 'ok'
    try:
        yield
    except BaseException:
        outcome = 'error'
        raise
    finally:
        logger.info('stage=%s status=%s seconds=%.6f counts=%s',
                    stage, outcome, perf_counter() - start, counts)
