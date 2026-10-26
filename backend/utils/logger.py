import os 
from datetime import *
import logging
import sys
import codecs
from logging.handlers import RotatingFileHandler

today_date = datetime.now().strftime('%Y-%m-%d')
# Base logs directory can be overridden via environment for staging/production
LOGS_BASE_DIR = os.getenv("LOGS_BASE_DIR", "logs")
component_dir = os.path.join(LOGS_BASE_DIR, "app")
log_subdirectory = os.path.join(component_dir, today_date)
os.makedirs(log_subdirectory, exist_ok=True)

# Define the path to the log file inside the day-wise subdirectory
log_file_path = os.path.join(log_subdirectory, 'app.log')

# Rotation settings
MAX_BYTES = 10 * 1024 * 1024  # 10MB
BACKUP_COUNT = 5

# Custom StreamHandler that safely handles Unicode characters
class EncodingSafeStreamHandler(logging.StreamHandler):
    def __init__(self, stream=None):
        super().__init__(stream)
        
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            
            # Handle Unicode characters safely for Windows console
            try:
                if isinstance(stream, type(sys.stdout)) and hasattr(stream, 'buffer'):
                    # For Windows, attempt to write directly to buffer with UTF-8 encoding
                    try:
                        # Try normal stream write first (works on most systems)
                        stream.write(msg)
                        stream.write(self.terminator)
                        self.flush()
                    except UnicodeEncodeError:
                        # If normal write fails, use UTF-8 encoding via buffer
                        stream.buffer.write((msg + self.terminator).encode('utf-8'))
                        self.flush()
                else:
                    stream.write(msg)
                    stream.write(self.terminator)
                    self.flush()
            except (UnicodeError, IOError):
                # Last resort fallback - replace problematic characters with '?'
                stream.write(msg.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding))
                stream.write(self.terminator)
                self.flush()
        except Exception:
            self.handleError(record)

# Configure logging with the custom handler and rotation
logging.basicConfig(
    level=logging.INFO,  # Set the default logging level
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  # Log message format
    handlers=[
        RotatingFileHandler(log_file_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding='utf-8'),  # Output logs to the file with rotation
        EncodingSafeStreamHandler()  # Use our custom handler for console output
    ]
)

logger = logging.getLogger('api_logger')