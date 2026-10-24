"""
Shared Enumerations for APAAR Nexus.
All values strictly follow AGENTS.md section 5.1 and 5.3.
Stored as strings in the database.
"""
from enum import Enum


class Role(str, Enum):
    AICTE_ADMIN = "AICTE_ADMIN"
    INSTITUTION_ADMIN = "INSTITUTION_ADMIN"
    STUDENT = "STUDENT"
    VERIFIER = "VERIFIER"
    AUDITOR = "AUDITOR"


class Classification(str, Enum):
    REGULAR = "REGULAR"
    SUPERNUMERARY = "SUPERNUMERARY"


class AdmissionCategory(str, Enum):
    OPEN = "OPEN"
    OBC = "OBC"
    SC = "SC"
    ST = "ST"
    EWS = "EWS"
    NRI = "NRI"
    OCI = "OCI"
    FN = "FN"


class EnrolmentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"
    GRADUATED = "GRADUATED"


class ValidationStatus(str, Enum):
    PENDING = "PENDING"
    VALID = "VALID"
    INVALID = "INVALID"
    FLAGGED = "FLAGGED"


class SubmissionStatus(str, Enum):
    PREVIEWED = "PREVIEWED"
    COMMITTED = "COMMITTED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


class AdmissionEventResult(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class RiskSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CaseStatus(str, Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED"
    DATA_ERROR_CONFIRMED = "DATA_ERROR_CONFIRMED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    RESOLVED = "RESOLVED"


class CaseActionType(str, Enum):
    UNDER_REVIEW = "UNDER_REVIEW"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    CONFIRM_DATA_ERROR = "CONFIRM_DATA_ERROR"
    DISMISS_FALSE_POSITIVE = "DISMISS_FALSE_POSITIVE"
    RESOLVE = "RESOLVE"


class CredentialStatus(str, Enum):
    ISSUED = "ISSUED"
    REVOKED = "REVOKED"


class VerificationResult(str, Enum):
    VERIFIED = "VERIFIED"
    INVALID = "INVALID"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class SharingClaim(str, Enum):
    DEGREE_AUTHENTICITY = "DEGREE_AUTHENTICITY"
    INSTITUTION = "INSTITUTION"
    PROGRAMME = "PROGRAMME"
    AWARD_YEAR = "AWARD_YEAR"
    STUDENT_NAME = "STUDENT_NAME"
    APAAR_MASKED = "APAAR_MASKED"


class SharingExpiryHours(str, Enum):
    HOURS_24 = "24"
    HOURS_72 = "72"
    HOURS_168 = "168"
