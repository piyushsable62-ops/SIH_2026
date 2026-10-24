"""
APAAR Adapter Service for APAAR Nexus.
Smart India Hackathon PS 26263.

PROTOTYPE NOTICE:
All APAAR validation in this module consists of synthetic format checks and a deterministic
mock adapter. This prototype is NOT connected to AICTE, APAAR, UIDAI, DigiLocker, or any
government production system. We do not invent an official APAAR algorithm or checksum.
Format check is strictly decoupled from identity verification.
"""
import re
import inspect
import dis
from typing import Optional, Tuple, Dict, Any, Union

from models.policy import SystemPolicy


def _get_apaar_regex_pattern() -> str:
    """Retrieve APAAR regex pattern from system policies or fallback to default 12 digits."""
    try:
        policy = SystemPolicy.query.filter_by(key="apaar_regex").first()
        if policy and isinstance(policy.value, str) and policy.value.strip():
            return policy.value.strip()
    except Exception:
        pass
    return r"^\d{12}$"


def _check_format(ref: Any) -> Tuple[bool, str]:
    """Internal core APAAR format check logic."""
    if ref is None:
        return False, "APAAR reference is required."

    clean_ref = str(ref).strip()
    if not clean_ref:
        return False, "APAAR reference cannot be empty."

    pattern = _get_apaar_regex_pattern()
    try:
        regex = re.compile(pattern)
    except re.error:
        regex = re.compile(r"^\d{12}$")

    if not regex.fullmatch(clean_ref):
        if not clean_ref.isdigit():
            return False, "APAAR reference must contain digits only."
        if len(clean_ref) != 12:
            return False, f"APAAR reference must be exactly 12 digits (got {len(clean_ref)})."
        return False, "APAAR reference does not match the required format."

    return True, "VALID"


def validate_format(ref: Any, detailed: Optional[bool] = None) -> Union[bool, Tuple[bool, str]]:
    """
    Validate APAAR identifier format using system policy regex (default 12 digits).
    Decoupled from identity verification: this is a synthetic format check only.

    Supports both:
    - Boolean return for truth-value checks: if validate_format(ref): ...
    - Tuple return (bool, reason) when unpacking: is_valid, reason = validate_format(ref)
    - Explicit control via detailed=True or detailed=False.
    """
    is_valid, reason = _check_format(ref)

    if detailed is True:
        return is_valid, reason
    if detailed is False:
        return is_valid

    # Inspect caller's bytecode to detect if caller expects a tuple (unpacking sequence)
    # or a boolean (truth-check / single assignment)
    try:
        frame = inspect.currentframe().f_back
        if frame:
            instrs = list(dis.get_instructions(frame.f_code))
            for ins in instrs:
                if ins.offset > frame.f_lasti:
                    if ins.opname in ("UNPACK_SEQUENCE", "UNPACK_EX"):
                        return is_valid, reason
                    break
    except Exception:
        pass

    return is_valid


def validate_format_with_reason(ref: Any) -> Tuple[bool, str]:
    """Explicit helper that always returns (is_valid, reason)."""
    return _check_format(ref)


def mask_apaar(ref: Optional[str]) -> str:
    """
    Central masking function for APAAR references.
    Replaces all but the last 4 characters with 'X' (e.g. 123456789012 -> XXXXXXXX9012).
    For short input (<= 4 chars), returns 'XXXXXXXX' to avoid leaking partial values.
    Safe against None and empty string (returns '').
    """
    if not ref:
        return ""

    clean = str(ref).strip()
    if not clean:
        return ""

    if len(clean) <= 4:
        return "XXXXXXXX"

    return f"XXXXXXXX{clean[-4:]}"


def mock_verify(ref: Optional[str]) -> Dict[str, Any]:
    """
    Deterministic mock APAAR verification adapter.
    Explicitly labeled as MOCK and not connected to any government service.

    Deterministic Rules:
    - Empty or invalid format: MOCK_NOT_FOUND
    - References starting with '9': MOCK_VERIFIED
    - References ending with '0000': MOCK_NOT_FOUND
    - All other valid format references: MOCK_UNAVAILABLE
    """
    is_valid = validate_format(ref, detailed=False)
    masked = mask_apaar(ref)

    notice = "MOCK — not connected to any government system"

    if not is_valid or not ref:
        return {
            "status": "MOCK_NOT_FOUND",
            "source": notice,
            "apaar_ref": masked,
            "details": "Identifier is missing or fails format validation."
        }

    clean_ref = str(ref).strip()

    if clean_ref.startswith("9"):
        return {
            "status": "MOCK_VERIFIED",
            "source": notice,
            "apaar_ref": masked,
            "details": "Deterministic mock match (starts with '9')."
        }

    if clean_ref.endswith("0000"):
        return {
            "status": "MOCK_NOT_FOUND",
            "source": notice,
            "apaar_ref": masked,
            "details": "Deterministic mock not found (ends with '0000')."
        }

    return {
        "status": "MOCK_UNAVAILABLE",
        "source": notice,
        "apaar_ref": masked,
        "details": "Deterministic mock verification service unavailable."
    }
