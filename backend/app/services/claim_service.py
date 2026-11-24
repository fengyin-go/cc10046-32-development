"""兼容入口：调度领取/续领服务。"""
from app.services.dispatch_claim import (  # noqa: F401
    ClaimService,
    DispatchClaimService,
    DispatchResponseInterrupted,
    claim_center,
    dispatch_claim_service,
)

ClaimCenter = DispatchClaimService
claim_service = claim_center
