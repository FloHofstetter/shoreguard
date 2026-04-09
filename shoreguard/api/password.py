"""Password policy validation."""

from __future__ import annotations


def validate_password(
    password: str, *, min_length: int = 8, require_complexity: bool = False
) -> str | None:
    """Validate a password against the configured policy.

    Args:
        password: The password to validate.
        min_length: Minimum required length.
        require_complexity: When True, require uppercase, lowercase, and digit.

    Returns:
        str | None: An error message string if invalid, or ``None`` if the password is acceptable.
    """
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters"
    if len(password) > 128:
        return "Password must be at most 128 characters"

    if require_complexity:
        missing: list[str] = []
        if not any(c.isupper() for c in password):
            missing.append("uppercase letter")
        if not any(c.islower() for c in password):
            missing.append("lowercase letter")
        if not any(c.isdigit() for c in password):
            missing.append("digit")
        if missing:
            return f"Password must contain at least one {', '.join(missing)}"

    return None


def check_password(password: str) -> str | None:
    """Validate a password using the application settings.

    Reads ``password_min_length`` and ``password_require_complexity`` from
    :class:`~shoreguard.settings.AuthSettings`.

    Args:
        password: The password to validate.

    Returns:
        str | None: An error message string if invalid, or ``None`` if acceptable.
    """
    from shoreguard.settings import get_settings

    settings = get_settings()
    return validate_password(
        password,
        min_length=settings.auth.password_min_length,
        require_complexity=settings.auth.password_require_complexity,
    )
