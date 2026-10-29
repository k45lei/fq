"""SQLAlchemy models.

Importing this package registers every model on `Base.metadata`, which the drift test relies
on. Import from here rather than from the individual modules.
"""

from app.models.base import (
    AdminPermission,
    AnswerType,
    Base,
    EntitlementKind,
    HollandTrait,
    QuestionCategory,
    ReportTier,
)
from app.models.career import CareerFamily, CareerGuidance, CareerPay
from app.models.identity import AdminAccount, User
from app.models.payment import DiscountCode, Entitlement, Purchase
from app.models.questionnaire import (
    Question,
    Questionnaire,
    QuestionnaireActivation,
    QuestionOption,
)
from app.models.report import Report, ReportCareerFamily
from app.models.response import (
    QuestionnaireResponse,
    ResponseAnswer,
    ResponseAnswerOption,
)

__all__ = [
    "AdminAccount",
    "AdminPermission",
    "AnswerType",
    "Base",
    "CareerFamily",
    "CareerGuidance",
    "CareerPay",
    "DiscountCode",
    "Entitlement",
    "EntitlementKind",
    "HollandTrait",
    "Purchase",
    "Question",
    "QuestionCategory",
    "QuestionOption",
    "Questionnaire",
    "QuestionnaireActivation",
    "QuestionnaireResponse",
    "Report",
    "ReportCareerFamily",
    "ReportTier",
    "ResponseAnswer",
    "ResponseAnswerOption",
    "User",
]
