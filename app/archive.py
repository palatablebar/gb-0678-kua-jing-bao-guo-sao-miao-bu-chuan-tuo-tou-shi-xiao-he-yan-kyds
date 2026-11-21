import csv,io,json
def build_archive(run,confirmed=None):
    """归档证据 CSV。

    只采纳“已确认”的抽检清单（每批唯一可执行核验样本）；待确认草稿一律不进入归档。
    样本行以 kind=sample 单独列出，并附替换原因与客服领取状态，与原始扫描证据互不覆盖。
    """
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","kind","source_lines","reason","evidence"])
    meta=run["manifest"]
    for row in run["stages"][-1]["rows"]:
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"accepted",json.dumps(row.get("_sources",[])),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in run["stages"][-1].get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"quarantine",json.dumps(row.get("_sources",[])),row.get("reason",""),json.dumps(row,ensure_ascii=False)])
    if confirmed:
        for sample in confirmed["samples"]:
            evidence={"parcel":sample["parcel"],"change":sample["change"],"claimed_by":sample.get("claimed_by"),
                      "sampling_version":confirmed["seq"],"sample_size":confirmed["sample_size"]}
            writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],"sample",
                             json.dumps([]),sample.get("reason",""),json.dumps(evidence,ensure_ascii=False)])
    return "﻿"+stream.getvalue()
