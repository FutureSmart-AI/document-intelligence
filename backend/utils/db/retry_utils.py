"""
Retry utilities for database operations to handle connection errors gracefully.
"""

import asyncio
import logging
from functools import wraps
from typing import Type, Tuple, Callable, Any

import asyncpg
from sqlalchemy.exc import OperationalError, InvalidRequestError

logger = logging.getLogger(__name__)

# Connection-related exceptions that should trigger retry
# Note: asyncpg.exceptions.InterfaceError is the actual exception type
# sqlalchemy wraps it, but we catch both
RETRYABLE_EXCEPTIONS = (
    asyncpg.exceptions.InterfaceError,
    OperationalError,
    InvalidRequestError,
)


def retry_db_connection(
    max_retries: int = 3,
    backoff_factor: float = 1.5,
    initial_delay: float = 0.1,
    exceptions: Tuple[Type[Exception], ...] = RETRYABLE_EXCEPTIONS
) -> Callable:
    """
    Decorator to retry async database functions on connection errors.
    
    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        backoff_factor: Exponential backoff multiplier (default: 1.5)
        initial_delay: Initial delay in seconds before first retry (default: 0.1)
        exceptions: Tuple of exception types to retry on (default: connection errors)
    
    Returns:
        Decorated function with retry logic
    
    Example:
        @retry_db_connection(max_retries=3, backoff_factor=1.5)
        async def db_function():
            async with async_session() as session:
                # DB operations
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    # Check if this is one of our retryable exceptions or a wrapped version
                    is_retryable = isinstance(e, exceptions)
                    
                    # Also check for SQLAlchemy-wrapped asyncpg exceptions
                    if not is_retryable and hasattr(e, 'orig'):
                        # SQLAlchemy wraps exceptions in .orig
                        is_retryable = isinstance(e.orig, exceptions)
                    
                    # Check if this is a connection-related error by message
                    error_msg = str(e).lower()
                    error_type_name = type(e).__name__.lower()
                    is_connection_error = (
                        "connection is closed" in error_msg or
                        "connection" in error_msg and ("closed" in error_msg or "stale" in error_msg or "lost" in error_msg) or
                        "stale" in error_msg or
                        "operationalerror" in error_type_name or
                        "interfaceerror" in error_type_name
                    )
                    
                    # Only retry if it's a retryable exception type AND connection-related
                    if is_retryable and is_connection_error:
                        last_exception = e
                        
                        if attempt < max_retries:
                            delay = initial_delay * (backoff_factor ** attempt)
                            logger.warning(
                                f"Database connection error in {func.__name__} (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                                f"Retrying in {delay:.2f} seconds..."
                            )
                            await asyncio.sleep(delay)
                        else:
                            logger.error(
                                f"Database connection error in {func.__name__} failed after {max_retries + 1} attempts: {e}"
                            )
                            raise
                    else:
                        # Non-retryable exception, raise immediately
                        logger.error(f"Non-retryable error in {func.__name__}: {e}")
                        raise
            
            # Should never reach here, but just in case
            if last_exception:
                raise last_exception
                
        return wrapper
    return decorator

