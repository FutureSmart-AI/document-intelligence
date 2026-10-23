import os 
from datetime import *
import logging
import sys
import codecs

today_date = datetime.now().strftime('%Y-%m-%d')
# Base logs directory can be overridden via environment for staging/production
LOGS_BASE_DIR = os.getenv("LOGS_BASE_DIR", "logs")
component_dir = os.path.join(LOGS_BASE_DIR, "celery")
log_subdirectory = os.path.join(component_dir, today_date)
os.makedirs(log_subdirectory, exist_ok=True)

# Define the path to the log file inside the day-wise subdirectory
log_file_path = os.path.join(log_subdirectory, 'celery_worker.log')

class EncodingSafeStreamHandler(logging.StreamHandler):
    def __init__(self, stream=None):
        super().__init__(stream)

    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            stream.write(msg)
            stream.write(self.terminator)
            self.flush()
        except (UnicodeError, IOError):     
            self.handleError(record)    

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        EncodingSafeStreamHandler() # Use our custom handler for console output
    ]
)

logger = logging.getLogger(__name__)    
