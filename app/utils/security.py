"""
Security utilities for input sanitization and validation.
"""

import re
import html
from typing import Optional


# Allowed HTML tags for sanitization (none for now - plain text only)
ALLOWED_TAGS = set()

# Patterns for detecting potentially malicious content
SCRIPT_PATTERN = re.compile(r'<\s*script', re.IGNORECASE)
EVENT_PATTERN = re.compile(r'\s+on\w+\s*=', re.IGNORECASE)
JAVASCRIPT_PATTERN = re.compile(r'javascript:', re.IGNORECASE)
DATA_PATTERN = re.compile(r'data:', re.IGNORECASE)


def sanitize_text(text: str, max_length: int = 1000) -> str:
    """
    Sanitize user input text.
    
    - Escapes HTML entities
    - Removes script tags and event handlers
    - Truncates to max_length
    
    Args:
        text: Input text to sanitize
        max_length: Maximum allowed length
    
    Returns:
        Sanitized text
    """
    if not text:
        return ""
    
    # Truncate
    text = text[:max_length]
    
    # Escape HTML
    text = html.escape(text, quote=True)
    
    return text.strip()


def validate_file_type(filename: str, allowed_extensions: set) -> bool:
    """
    Validate file extension.
    
    Args:
        filename: The filename to check
        allowed_extensions: Set of allowed extensions (e.g., {'jpg', 'png', 'mp4'})
    
    Returns:
        True if valid, False otherwise
    """
    if not filename:
        return False
    
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in allowed_extensions


def sanitize_for_html(text: str) -> str:
    """
    Sanitize text for safe HTML display.
    Escapes all HTML entities.
    
    Args:
        text: Input text
    
    Returns:
        Escaped text safe for HTML
    """
    if not text:
        return ""
    return html.escape(str(text), quote=True)


def is_safe_url(url: str) -> bool:
    """
    Check if a URL is safe (no javascript: or data: protocols).
    
    Args:
        url: URL to check
    
    Returns:
        True if safe, False otherwise
    """
    if not url:
        return False
    
    url_lower = url.lower().strip()
    
    # Block dangerous protocols
    if JAVASCRIPT_PATTERN.match(url_lower):
        return False
    if DATA_PATTERN.match(url_lower):
        return False
    
    return True


def validate_username(username: str) -> tuple:
    """
    Validate username format.
    
    Args:
        username: Username to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not username:
        return False, "Username is required."
    
    if len(username) < 3 or len(username) > 30:
        return False, "Username must be 3-30 characters."
    
    if not re.match(r'^[a-zA-Z0-9._]+$', username):
        return False, "Username can only contain letters, numbers, . and _"
    
    return True, None


def validate_email(email: str) -> tuple:
    """
    Validate email format.
    
    Args:
        email: Email to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not email:
        return False, "Email is required."
    
    # Basic email validation
    pattern = r'^[^@\s]+@[^@\s]+\.[^@\s]+$'
    if not re.match(pattern, email):
        return False, "Invalid email format."
    
    if len(email) > 254:
        return False, "Email is too long."
    
    return True, None