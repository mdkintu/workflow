"""Phone number normalisation.

Accepts the shapes staff actually type: "0772 123-456", "772123456",
"+256772123456", "256772123456". Uganda national numbers are 9 digits.
"""

import re


def normalise(raw: str, country_code: str = "256") -> str:
    """Returns an E.164 string like "+256772123456", or raises ValueError."""
    if not raw:
        raise ValueError("Phone number is required.")

    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError(f"{raw!r} is not a valid phone number.")

    if digits.startswith(country_code) and len(digits) == len(country_code) + 9:
        national = digits[len(country_code) :]
    elif digits.startswith("0") and len(digits) == 10:
        national = digits[1:]
    elif len(digits) == 9:
        national = digits
    else:
        raise ValueError(f"{raw!r} is not a valid phone number.")

    if len(national) != 9:
        raise ValueError(f"{raw!r} is not a valid phone number.")

    return f"+{country_code}{national}"
