import threading
from psycopg2 import pool
from services.celery_logger import logger
from services import celery_setup_env

class DatabasePool:
    """Singleton connection pool manager for database operations (sync version)."""

    _instance = None
    _pool = None
    _lock = None  # threading.Lock for sync

    @classmethod
    def get_instance(cls):
        """Get the singleton instance with lazy initialization."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_lock(self):
        if self._lock is None:
            self._lock = threading.Lock()
        return self._lock

    def get_pool(self):
        lock = self._get_lock()
        with lock:
            if self._pool is None:
                logger.info("Creating database connection pool (sync)...")
                try:
                    self._pool = pool.SimpleConnectionPool(
                        minconn=5,
                        maxconn=20,
                        user=celery_setup_env.POSTGRES_USER,
                        password=celery_setup_env.POSTGRES_PASSWORD,
                        database=celery_setup_env.POSTGRES_DB_NAME,
                        host=celery_setup_env.POSTGRES_HOST,
                        port=celery_setup_env.POSTGRES_PORT,
                    )
                    logger.info("Database connection pool created successfully (sync)")
                except Exception as e:
                    logger.error(f"Failed to create database pool: {e}")
                    raise
        return self._pool

    def close(self):
        if self._lock is not None:
            with self._lock:
                if self._pool:
                    logger.info("Closing database connection pool (sync)...")
                    self._pool.closeall()
                    self._pool = None
                    logger.info("Database connection pool closed (sync)")
        else:
            if self._pool:
                logger.info("Closing database connection pool (sync)...")
                self._pool.closeall()
                self._pool = None
                logger.info("Database connection pool closed (sync)")

    def check_pool_health(self):
        if not self._pool:
            logger.warning("Connection pool does not exist (sync)")
            return None
        try:
            conn = self._pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    result = cur.fetchone()
                return result
            finally:
                self._pool.putconn(conn)
        except Exception as e:
            logger.error(f"Connection pool health check failed (sync): {e}")
            return None

# Create a global instance
db_pool_manager = DatabasePool()

