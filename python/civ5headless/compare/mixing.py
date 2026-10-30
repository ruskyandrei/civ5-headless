"""Whether two runs had the same AI logging, which with some DLLs decides which game a seed plays (docs/ai-logging.md).

The comparison tools warn when they know the two differ. A path that is not in a run folder (a bare log, a reference
recorded from the real game) says nothing, so it gets no warning.
"""
import json
from pathlib import Path


def ai_logging(status, host_options):
    """Whether a game played with AI logging: status.json's aiLogging, or for older runs host-report.json's options; None
    when neither says."""
    if status.get('aiLogging') is not None:
        return status['aiLogging']
    if host_options.get('gameLogging') is None or host_options.get('gameLogFlags') is None:
        return None
    return bool(host_options['gameLogging'] and host_options['gameLogFlags'] & 1)


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def of_run(path):
    """ai_logging of the run that path belongs to (a run folder, its Logs folder or a log in either), else None."""
    p = Path(path).resolve()
    folder = p.parent if p.is_file() else p
    if folder.name.lower() == 'logs':
        folder = folder.parent
    status = _read(folder / 'status.json')
    if status is None:
        return None
    return ai_logging(status, (_read(folder / 'host-report.json') or {}).get('options') or {})


def check(a, b, name_a=None, name_b=None):
    """([A's AI logging, B's], a warning when they differ, else None) for the runs that paths a and b belong to; name_a and
    name_b: what to call them in the warning (default: the paths)."""
    logging_a, logging_b = of_run(a), of_run(b)
    if logging_a is None or logging_b is None or logging_a == logging_b:
        return [logging_a, logging_b], None
    return [logging_a, logging_b], (
        f"warning: these games had different AI logging: {name_a or a} {'on' if logging_a else 'off'}, "
        f"{name_b or b} {'on' if logging_b else 'off'}. With some DLLs that alone plays a different game, so a difference "
        'here may say nothing else (docs/ai-logging.md)')
