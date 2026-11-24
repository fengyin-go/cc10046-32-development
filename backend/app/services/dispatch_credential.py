"""兼容入口：调度批次凭据服务的不同命名/模块路径。"""
from app.services.dispatch_claim import (  # noqa: F401
    ClaimService,
    DispatchClaimService,
    DispatchConfirmationInterrupted,
    DispatchCredentialService,
    DispatchResponseInterrupted,
    ShardPlanService,
    bind_dispatch_facade,
    claim_center,
    dispatch_claim_service,
)

# 该领域最可能的主类名。
DispatchCredentialCenter = DispatchClaimService
CredentialService = DispatchClaimService
dispatch_credential_service = dispatch_claim_service
credential_center = claim_center
