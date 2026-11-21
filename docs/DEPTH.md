# 深度登记 跨境包裹扫描补传与妥投时效核验

规模：中型。使用者：跨境集运客服质控员。核心规则：以事件发生时刻重建路径，补传不改变既有妥投时间；承诺时钟按目的地节假日停表。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 扫描采信 | 查看并运行扫描采信 | `/api/scans` | `app/modules/m1_scans/rules.py` | `tests/test_m1.py` |
| M2 节点映射 | 查看并运行节点映射 | `/api/nodes` | `app/modules/m2_nodes/rules.py` | `tests/test_m2.py` |
| M3 时区归整 | 查看并运行时区归整 | `/api/timezones` | `app/modules/m3_timezones/rules.py` | `tests/test_m3.py` |
| M4 路径裁决 | 查看并运行路径裁决 | `/api/paths` | `app/modules/m4_paths/rules.py` | `tests/test_m4.py` |
| M5 时效计算 | 查看并运行时效计算 | `/api/timeliness` | `app/modules/m5_timeliness/rules.py` | `tests/test_m5.py` |
| M6 赔付对账 | 查看并运行赔付对账 | `/api/compensation` | `app/modules/m6_compensation/rules.py` | `tests/test_m6.py` |
| M7 批次归档 | 查看并运行批次归档 | `/api/archives` | `app/modules/m7_archives/rules.py` | `tests/test_m7.py` |
| M8 月末抽检 | 固定数量超时抽检、补传重抽与确认 | `/sampling`、`/api/sampling` | `app/modules/m8_sampling/rules.py`、`app/sampling_store.py` | `tests/test_m8.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码 | 默认解码失败 | 回退GB18030 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_legacy_encoding_real_sample` |
| TRAP-02 | BOM | 首列名多字符 | utf-8-sig | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_bom_real_sample` |
| TRAP-03 | 带引号换行 | 拆成两条 | csv逻辑行 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_quoted_newline_is_one_record` |
| TRAP-05 | 列数不匹配 | 静默错列 | 隔离原行 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_bad_columns_real_sample` |
| TRAP-08 | 前导零ID | 编码变形 | 全程字符串 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_identifier_preserved_real_sample` |
| TRAP-14 | 重复键冲突 | 后写覆盖 | 保留首条并隔离冲突行 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_duplicate_conflict_real_sample` |
| TRAP-18 | 空态三态 | 一律成功 | 分别错误码 | `app/modules/m1_scans/rules.py` | `tests/test_m1.py::test_three_empty_states` |
| TRAP-26 | 旧版本提交 | 覆盖新结果 | 版本条件更新 | `app/store.py` | `tests/test_e2e.py::test_stale_review_409` |
| TRAP-06 | 全角时限 | 数字转换失败 | Decimal归一 | `app/values.py` | `tests/test_m3.py::test_numeric_normalization` |
| TRAP-07 | 时限哨兵 | 按零赔付 | 隔离缺失 | `app/values.py` | `tests/test_m3.py::test_real_sentinel_not_zero` |
| TRAP-11 | 目的地偏移 | 同刻多桶 | UTC比较 | `app/values.py` | `tests/test_m3.py::test_equivalent_timezone` |
| TRAP-12 | DST自然日 | 固定24小时 | 按当地午夜切片 | `app/modules/m5_timeliness/rules.py` | `tests/test_m5.py::test_dst_actual_elapsed_hours` |
| TRAP-13 | 迟到妥投补传 | 按上传时间赔付 | 发生时间重建 | `app/modules/m4_paths/rules.py` | `tests/test_m4.py::test_late_upload_event_time_decides` |
| TRAP-23 | 假日端点 | 误算全天 | 当地日半开切段 | `app/modules/m5_timeliness/rules.py` | `tests/test_m5.py::test_holiday_full_day_stops` |
| TRAP-30 | 补传覆盖已确认样本 | 按最新异常直接改写清单，客服样本反复变化 | 已确认清单冻结，补传只追加“待确认”新版本，确认后才替换 | `app/sampling_store.py` | `tests/test_m8.py::test_resupination_keeps_confirmed_and_adds_pending_version` |
| TRAP-31 | 已领取样本被静默替换 | 重抽剔除已离场的领取样本 | 领取样本锁定保留；离场则 409 阻断交人工，绝不静默替换 | `app/modules/m8_sampling/rules.py` | `tests/test_m8.py::test_claimed_sample_always_retained` |
| TRAP-32 | 重抽数量漂移/重复 | 补位后样本变多或重复 | 等量替换，结果数量与唯一性强校验，异常即整体回滚 | `app/modules/m8_sampling/rules.py` | `tests/test_m8.py::test_revise_equal_swap_with_reasons` |
| TRAP-33 | 归档读到草稿 | 待确认清单进入证据 | 归档只采纳每批唯一“已确认”版本，草稿不导出 | `app/archive.py` | `tests/test_m8.py::test_archive_reads_confirmed_only` |

## 对账规则

输入原始逻辑行=最终采纳来源行+隔离来源行+重复行+空白行+分段表头。派生记录用_sources保存完整来源，因此分组不丢行。业务预期合计与上游申报合计之差等于逐行差异之和。结果按输入摘要、规则版本及参数产生唯一批次，事务写入；同源重跑只复用已有批次。

## 已知不覆盖

4MB以上文件需预先按业务键分批；不模拟生产支付与外部回调。人工复核只变更批次状态，不改写原始记录。

## 月末抽检裁决（运营 vs 质控）

补传改变超时异常集合时，**不覆盖原抽检清单，也不直接替换；而是生成“待确认”的新版本，由质控确认后才提升为可执行清单**。每批至多一条“已确认”清单，即该批唯一可执行核验样本：

- 抽检池为 M5 判定“超时”的包裹，按包裹号确定排序取前 N 个（N 固定），可重放、字符串业务键保留前导零。
- 补传重抽为等量替换：仍在超时池的原样本保留（客服已领取的强制锁定并标注原因），新进超时包裹按确定性顺序补位；数量恒定、无新增、无重复。
- 已领取样本若因补传不再超时，重抽返回 409（`CLAIMED_LEFT_POOL`）交人工处置，禁止静默替换。
- 清单版本只追加（待确认→已确认→被新版本取代后标记已归档，历史保留）；无任何物理删除，原始扫描与隔离行不受影响。
- 同输入重试幂等：异常集合未变不产生新版本；所有写入在 SQLite 事务内完成，失败回滚到旧版。
- 归档导出只读取已确认清单（`kind=sample` 行附替换原因与领取状态），待确认草稿不进入证据。
