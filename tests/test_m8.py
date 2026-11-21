import csv,io
import pytest
from app import store,sampling_store as ss
from app.errors import DomainError
from app.modules.m8_sampling import rules

# ---------- 构造合成批次（走真实流水线与真实持久层） ----------
def make_csv(parcels,promised="0"):
    buf=io.StringIO();w=csv.writer(buf)
    w.writerow(["scan_id","parcel","node","event_time","received_time","destination",
                "promised_hours","declared_value","reported","note"])
    for i,p in enumerate(parcels):
        base=100+i*10
        w.writerow([base,p,"收件","2026-11-02T00:00:00Z","2026-11-02T01:00:00Z","JP","24","200","0","集运"])
        w.writerow([base+1,p,"妥投","2026-11-06T00:00:00Z","2026-11-06T01:00:00Z","JP",promised,"200","0","集运"])
    return buf.getvalue()

def run_batch(client,parcels,promised="0"):
    r=client.post("/api/runs",json={"csv":make_csv(parcels,promised)});assert r.status_code==200,r.get_json()
    return r.get_json()["run_id"]

def overdue(run_id): return rules.overdue_pool(store.get(run_id)["stages"][4]["rows"])

# ---------- 纯规则：确定性、固定数量 ----------
def test_deterministic_fixed_size_selection():
    rows=[{"parcel":"000%d"%i,"sla":"超时"} for i in range(1,7)]
    a=rules.build_draft(rows,3);b=rules.build_draft(rows,3)
    assert [s["parcel"] for s in a["samples"]]==["0001","0002","0003"]
    assert a==b and len(a["samples"])==3  # 可重放、数量固定

def test_quota_cannot_exceed_pool():
    with pytest.raises(DomainError) as e: rules.build_draft([{"parcel":"1","sla":"超时"}],2)
    assert e.value.code=="POOL_TOO_SMALL"

def test_string_identifier_leading_zero_kept():
    plan=rules.build_draft([{"parcel":"007","sla":"超时"}],1)
    assert plan["samples"][0]["parcel"]=="007"

# ---------- 补传重抽：保留+等量替换，数量不增不重 ----------
def test_revise_equal_swap_with_reasons():
    active=[{"parcel":"0001","reason":"x","change":"NEW_POOL","claimed_by":None},
            {"parcel":"0002","reason":"x","change":"NEW_POOL","claimed_by":None}]
    # 新超时池：0001 仍超时，0002 达标，0003 新进
    rows=[{"parcel":"0001","sla":"超时"},{"parcel":"0003","sla":"超时"},{"parcel":"0002","sla":"达标"}]
    plan=rules.revise_draft(rows,2,active)
    ids=[s["parcel"] for s in plan["samples"]]
    assert sorted(ids)==["0001","0003"] and len(ids)==len(set(ids))==2  # 等量、唯一
    assert {d["parcel"]:d["reason"] for d in plan["diff"]["removed"]}=={"0002":rules.REASONS["NO_LONGER_LATE"]}
    assert {d["parcel"]:d["reason"] for d in plan["diff"]["added"]}=={"0003":rules.REASONS["ENTER_LATE"]}

def test_revise_blocked_when_claimed_sample_leaves_pool():
    active=[{"parcel":"0001","reason":"x","change":"NEW_POOL","claimed_by":"cs01"}]
    rows=[{"parcel":"0001","sla":"达标"},{"parcel":"0002","sla":"超时"}]
    with pytest.raises(DomainError) as e: rules.revise_draft(rows,1,active)
    assert e.value.code=="CLAIMED_LEFT_POOL" and e.value.status==409

def test_claimed_sample_always_retained():
    active=[{"parcel":"0001","reason":"x","change":"NEW_POOL","claimed_by":"cs01"},
            {"parcel":"0002","reason":"x","change":"NEW_POOL","claimed_by":None}]
    rows=[{"parcel":"0001","sla":"超时"},{"parcel":"0003","sla":"超时"}]
    plan=rules.revise_draft(rows,2,active)
    first=plan["samples"][0]
    assert first["parcel"]=="0001" and first["claimed_by"]=="cs01"
    assert first["reason"]==rules.REASONS["CLAIMED_RETAINED"]

# ---------- 端到端：版本化与确认 ----------
def test_draft_then_confirm_becomes_unique_executable(client):
    rid=run_batch(client,["0001","0002","0003"])
    r=client.post("/api/sampling/drafts",json={"run_id":rid,"sample_size":2});assert r.status_code==200
    assert r.get_json()["version"]["status"]=="待确认"
    # 未确认前没有可执行清单
    assert client.get("/api/sampling?run_id="+rid).get_json()["active"] is None
    r=client.post("/api/sampling/confirm",json={"run_id":rid,"seq":1});assert r.status_code==200
    active=client.get("/api/sampling?run_id="+rid).get_json()["active"]
    assert active["status"]=="已确认" and len(active["samples"])==2

def test_resupination_keeps_confirmed_and_adds_pending_version(client):
    rid=run_batch(client,["0001","0002","0003"])  # 首批，三个都超时
    client.post("/api/sampling/drafts",json={"run_id":rid,"sample_size":2})
    client.post("/api/sampling/confirm",json={"run_id":rid,"seq":1})
    before=ss.get_active(rid)
    # 补传：候选批次里 0001 达标、0002/0003/0004 超时（异常集合改变）
    cand=run_batch(client,["0002","0003","0004"])
    r=client.post("/api/sampling/drafts",json={"run_id":rid,"candidate_run_id":cand,"sample_size":2})
    assert r.status_code==200 and r.get_json()["changed"] is True
    # 已确认清单原样保留（裁决：不覆盖）
    after=ss.get_active(rid)
    assert [s["parcel"] for s in after["samples"]]==[s["parcel"] for s in before["samples"]]
    versions=ss.list_versions(rid)
    assert [v["seq"] for v in versions]==[1,2]
    assert versions[0]["status"]=="已确认" and versions[1]["status"]=="待确认"
    # 确认新版本后，旧版本归档保留、新版本成为唯一可执行
    client.post("/api/sampling/confirm",json={"run_id":rid,"seq":2})
    seqs={v["seq"]:v["status"] for v in ss.list_versions(rid)}
    assert seqs=={1:"已归档",2:"已确认"}
    assert ss.get_active(rid)["seq"]==2

def test_unchanged_resupination_does_not_add_version(client):
    rid=run_batch(client,["0001","0002","0003"])
    client.post("/api/sampling/drafts",json={"run_id":rid,"sample_size":2})
    client.post("/api/sampling/confirm",json={"run_id":rid,"seq":1})
    cand=run_batch(client,["0001","0002","0003"])  # 相同异常集合
    r=client.post("/api/sampling/drafts",json={"run_id":rid,"candidate_run_id":cand,"sample_size":2})
    assert r.get_json()["changed"] is False
    assert len(ss.list_versions(rid))==1  # 同批重试不新增样本/版本

def test_retry_same_draft_is_idempotent(client):
    rid=run_batch(client,["0001","0002"])
    for _ in range(2):
        client.post("/api/sampling/drafts",json={"run_id":rid,"sample_size":1})
    versions=ss.list_versions(rid)
    assert len(versions)==1 and len(versions[0]["samples"])==1  # 不重复、不新增

def test_claim_requires_confirmed_and_locks_then_blocks_silent_swap(client):
    rid=run_batch(client,["0001","0002","0003"])
    client.post("/api/sampling/drafts",json={"run_id":rid,"sample_size":2})
    # 未确认不能领取
    r=client.post("/api/sampling/claim",json={"run_id":rid,"parcel":"0001","claimed_by":"cs01"})
    assert r.status_code==409
    client.post("/api/sampling/confirm",json={"run_id":rid,"seq":1})
    assert client.post("/api/sampling/claim",json={"run_id":rid,"parcel":"0001","claimed_by":"cs01"}).status_code==200
    # 重复领取幂等；他人不可抢占
    assert client.post("/api/sampling/claim",json={"run_id":rid,"parcel":"0001","claimed_by":"cs01"}).status_code==200
    assert client.post("/api/sampling/claim",json={"run_id":rid,"parcel":"0001","claimed_by":"cs02"}).status_code==409
    # 已领取样本离开超时池 -> 重抽阻断
    cand=run_batch(client,["0002","0003","0004"])  # 0001 不再超时
    r=client.post("/api/sampling/drafts",json={"run_id":rid,"candidate_run_id":cand,"sample_size":2})
    assert r.status_code==409 and r.get_json()["code"]=="CLAIMED_LEFT_POOL"

def test_archive_reads_confirmed_only(client):
    rid=run_batch(client,["0001","0002","0003"])
    # 无确认清单也能导出，但不含 sample 行
    base=client.get("/api/runs/"+rid+"/archive").data.decode("utf-8-sig")
    assert ",sample," not in base
    client.post("/api/sampling/drafts",json={"run_id":rid,"sample_size":2})
    # 待确认草稿不进归档
    assert ",sample," not in client.get("/api/runs/"+rid+"/archive").data.decode("utf-8-sig")
    client.post("/api/sampling/confirm",json={"run_id":rid,"seq":1})
    out=client.get("/api/runs/"+rid+"/archive").data.decode("utf-8-sig")
    sample_lines=[ln for ln in csv.reader(io.StringIO(out)) if ln and ln[3]=="sample"]
    assert len(sample_lines)==2  # 归档读取的是已确认清单的固定数量

def test_failed_refresh_rolls_back_keeps_old_version(client):
    rid=run_batch(client,["0001","0002","0003"])
    client.post("/api/sampling/drafts",json={"run_id":rid,"sample_size":2})
    client.post("/api/sampling/confirm",json={"run_id":rid,"seq":1})
    before=ss.list_versions(rid)
    # 非法数量触发领域错误：事务回滚，版本链保持旧版
    with pytest.raises(DomainError):
        ss.create_or_refresh_draft(rid,rid,0)
    assert ss.list_versions(rid)==before
    assert ss.get_active(rid)["seq"]==1

def test_original_scans_never_deleted(client):
    rid=run_batch(client,["0001","0002"])
    original=store.get(rid)
    client.post("/api/sampling/drafts",json={"run_id":rid,"sample_size":1})
    client.post("/api/sampling/confirm",json={"run_id":rid,"seq":1})
    client.post("/api/sampling/claim",json={"run_id":rid,"parcel":"0001","claimed_by":"cs01"})
    again=store.get(rid)
    assert again["run_id"]==rid and again["stages"][0]["rows"]==original["stages"][0]["rows"]
    # 持久层不提供任何删除路径
    import inspect
    assert "DELETE" not in inspect.getsource(ss)

def test_sampling_page_renders(client):
    run_batch(client,["0001","0002"])
    assert client.get("/sampling").status_code==200

def test_overview_empty_when_no_run(client):
    assert client.get("/api/sampling").get_json()=={"run_id":None,"versions":[],"active":None}
