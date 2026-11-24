"""S11 调度批次凭据 / 来源分片计划领取历史 回归测试（自清洁）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.services.dispatch_claim import (
    DispatchClaimService,
    DispatchResponseInterrupted,
    claim_center,
)
from app.services.crawl_schedule import CrawlScheduleService
from app.services.batch_tracker import BatchTrackerService
from app.store import store

SOURCE = "SRC-TEST-S11"
BATCH_IDS = {"T-B-OLD-2", "T-B-NEW-1"}


@pytest.fixture(autouse=True)
def isolated_dispatch():
    claim_center.reset()
    yield
    claim_center.reset()
    rows = store.rows("batch_tracker")
    rows[:] = [
        r for r in rows
        if r.get("批次标识") not in BATCH_IDS
        and SOURCE not in str(r.get("调度任务", ""))
    ]


def test_plan_versions_and_shard_ranges():
    svc = DispatchClaimService()
    old = svc.confirm_plan(source=SOURCE, version=1, page_start=1, page_end=5, shard_size=2)
    new = svc.confirm_plan(source=SOURCE, version=2, page_start=4, page_end=7, shard_size=2)
    assert [p["page_range"] for p in (old, new)] == ["1-5", "4-7"]
    assert [s["pages"] for s in old["shards"]] == [[1, 2], [3, 4], [5]]
    assert [s["pages"] for s in new["shards"]] == [[4, 5], [6, 7]]
    # 旧计划对象不随新计划改变
    again_old = svc.get_plan(plan_id=old["plan_id"])
    assert again_old["pages"] == [1, 2, 3, 4, 5]


def test_interrupted_then_same_application_returns_original_batch():
    svc = DispatchClaimService()
    old_plan = svc.confirm_plan(source=SOURCE, version=1, page_start=1, page_end=5, shard_size=2)
    with pytest.raises(DispatchResponseInterrupted) as exc:
        svc.claim_shard(
            application_id="T-APP-OLD", plan_id=old_plan["plan_id"],
            shard_index=2, batch_id="T-B-OLD-2", disconnect=True,
        )
    assert exc.value.credential["batch_id"] == "T-B-OLD-2"

    tracker_rows_before = [r for r in store.rows("batch_tracker") if r.get("批次标识") == "T-B-OLD-2"]
    assert len(tracker_rows_before) == 1

    retried = svc.claim_shard(application_id="T-APP-OLD")
    assert retried["batch_id"] == "T-B-OLD-2"
    assert retried["replayed"] is True
    assert retried["plan_version"] == 1
    assert retried["pages"] == [3, 4]

    # 不得再次创建批次 / 追踪行
    tracker_rows_after = [r for r in store.rows("batch_tracker") if r.get("批次标识") == "T-B-OLD-2"]
    assert len(tracker_rows_after) == 1

    history = svc.claim_history("T-APP-OLD")
    assert history[0]["kind"] == "first_claim"
    assert any(e["kind"] == "resume" for e in history)


def test_full_narrative_board_and_separate_views():
    scenario = claim_center.load_scenario(SOURCE)
    old_cred = scenario["interrupted_credential"]
    assert old_cred["pages"] == [3, 4]

    new_claim = claim_center.claim_shard(
        application_id="T-APP-NEW", source=SOURCE,
        plan_id=scenario["new_plan"]["plan_id"], shard_index=1, batch_id="T-B-NEW-1",
    )
    assert new_claim["plan_version"] == 2
    assert new_claim["pages"] == [4, 5]

    board = claim_center.source_board(SOURCE)
    assert board["new_batch_pages"] == [4, 5]
    assert board["old_pending_pages"] == [3, 4]
    assert board["overlap_pages"] == [4]

    # 续回旧领取：原批次 + 两个计划仍可单独查看
    resumed = claim_center.resume_claim("APP-OLD-SHARD2")
    assert resumed["batch_id"] == "B-OLD-SHARD2"
    assert resumed["pages"] == [3, 4]
    plans = {p["version"]: p for p in board["plans"]}
    assert plans[1]["page_range"] == "1-5"
    assert plans[2]["page_range"] == "4-7"

    tracked = claim_center.get_batch_range("APP-OLD-SHARD2")
    assert tracked["batch_id"] == "B-OLD-SHARD2"
    assert tracked["pages"] == [3, 4]
    assert tracked["计划版本"] == "v1"


def test_service_facades_expose_api():
    schedule = CrawlScheduleService()
    tracker = BatchTrackerService()
    for svc in (schedule, tracker):
        assert callable(svc.claim_shard)
        assert callable(svc.resume_claim)
        assert callable(svc.source_board)
        assert callable(svc.get_batch_range)

    plan = schedule.confirm_plan(source=SOURCE, page_start=1, page_end=3)
    cred = schedule.claim_shard(application_id="T-FACADE", plan_id=plan["plan_id"])
    tracked = tracker.domain_track_by_credential(cred["credential_id"])
    assert tracked["batch_id"] == cred["batch_id"]
