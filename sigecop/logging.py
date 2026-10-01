import json
import logging
import traceback
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record):
        evento = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "nivel": record.levelname,
            "logger": record.name,
            "mensaje": record.getMessage(),
        }
        if hasattr(record, "resultado"):
            evento["resultado"] = record.resultado
        if record.exc_info:
            evento["excepcion"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(evento, ensure_ascii=False)
