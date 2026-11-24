"""兼容入口：来源分片计划服务。"""
from app.services.dispatch_claim import (  # noqa: F401
    DispatchClaimService,
    ShardPlanService,
    build_page_shards,
    claim_center,
    dispatch_claim_service,
)

SourceShardPlanService = DispatchClaimService
ShardPlanCenter = DispatchClaimService
shard_plan_service = claim_center
