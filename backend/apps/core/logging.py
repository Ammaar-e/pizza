import json
import logging
import re

_PATTERNS = [
    (re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(bearer |api-key |je-api-key )?[A-Za-z0-9._\-~+/=]+"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)((?:client_secret|signing_secret|password|api_key|secret|token)['\"]?\s*[:=]\s*['\"]?)[^\s'\",&}]+"), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("provider", "store", "order_id", "event_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)
