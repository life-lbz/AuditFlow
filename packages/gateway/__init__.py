"""模型网关（文档 §17）：统一模型接口，屏蔽本地 stub 与远端真实模型的差异。"""

from .base import (
    AgentAction,
    DocumentType,
    ExtractionField,
    ExtractionRequest,
    ExtractionResult,
    ModelCallRecord,
    ModelGateway,
    VerificationRequest,
    VerificationResult,
)
from .factory import build_gateway, get_gateway, reset_gateway
from .remote import RemoteModelGateway
from .stub import REQUIRED_FIELDS, StubModelGateway

__all__ = [
    "AgentAction",
    "DocumentType",
    "ExtractionField",
    "ExtractionRequest",
    "ExtractionResult",
    "ModelCallRecord",
    "ModelGateway",
    "VerificationRequest",
    "VerificationResult",
    "RemoteModelGateway",
    "StubModelGateway",
    "REQUIRED_FIELDS",
    "build_gateway",
    "get_gateway",
    "reset_gateway",
]
