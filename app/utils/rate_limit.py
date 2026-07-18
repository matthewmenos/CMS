"""
Rate limiting utility for Flask endpoints.
Simple in-memory rate limiter with configurable limits per endpoint.
"""

import time
from functools import wraps
from collections import defaultdict
from threading import Lock

# Thread-safe storage for rate limit counters
_rate_limit_data = defaultdict(list)
_rate_limit_lock = Lock()


def rate_limit(max_requests: int = 10, window_seconds: int = 60):
    """
    Decorator to rate limit an endpoint.
    
    Args:
        max_requests: Maximum number of requests allowed in the window
        window_seconds: Time window in seconds
    
    Returns:
        Decorated function that returns 429 if rate limit exceeded
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            from flask import request, jsonify
            
            # Create a key based on IP and endpoint
            key = f"{request.remote_addr}:{f.__name__}"
            now = time.time()
            
            with _rate_limit_lock:
                # Clean old entries
                _rate_limit_data[key] = [
                    ts for ts in _rate_limit_data[key]
                    if now - ts < window_seconds
                ]
                
                # Check limit
                if len(_rate_limit_data[key]) >= max_requests:
                    return jsonify({
                        "ok": False,
                        "error": f"Rate limit exceeded. Try again in {window_seconds} seconds."
                    }), 429
                
                # Record this request
                _rate_limit_data[key].append(now)
            
            return f(*args, **kwargs)
        return decorated
    return decorator


# Predefined rate limit decorators for common use cases
login_rate_limit = rate_limit(max_requests=5, window_seconds=60)
api_rate_limit = rate_limit(max_requests=60, window_seconds=60)
upload_rate_limit = rate_limit(max_requests=10, window_seconds=60)
giving_rate_limit = rate_limit(max_requests=5, window_seconds=60)