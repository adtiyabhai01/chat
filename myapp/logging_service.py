import logging
from datetime import datetime, timezone as _tz


class DatabaseLogHandler(logging.Handler):
    """Custom logging handler that stores logs in database"""
    
    def emit(self, record):
        try:
            from .models import AppLog
            log_entry = AppLog(
                level=record.levelname,
                logger_name=record.name,
                message=self.format(record),
                module=record.module,
                function=record.funcName,
                line_number=record.lineno,
                # Aware UTC instant: correct no matter the server's own timezone.
                # Display is converted to IST by _ist_full() in the views.
                timestamp=datetime.fromtimestamp(record.created, tz=_tz.utc)
            )
            log_entry.save()
        except Exception:
            self.handleError(record)


def get_logger(name):
    """Get a configured logger instance"""
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        
        db_handler = DatabaseLogHandler()
        db_handler.setLevel(logging.DEBUG)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        db_handler.setFormatter(formatter)
        logger.addHandler(db_handler)
    
    return logger
