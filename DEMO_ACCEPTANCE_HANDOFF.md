# MT5 Demo 验收问题交接

- 日期：2026-11-14
- 服务端：MT5 Web Terminal，bootstrap 报告 **build 6320**
- 范围：连接、登录、只读查询、行情订阅、历史K线、迟到 ping、主动断线后的自动重连
- 明确未做：下单、改单、撤单、账户修改、提交、推送、发布
- 保密：本文不包含 login、password、token、session、CID、账户/持仓内容或原始敏感帧。本地 `.env` 不得提交。

## 1. 交接摘要

用户提供的 build 6320 登录修复已经生效，登录不再是阻塞项。本轮最重要的新发现是：

1. **已确认：同 command 的迟到响应会完成后续请求，当前 FIFO 存在真实串配。**
2. **已确认：tick 退订收到非零响应码，但客户端忽略它并把本地状态更新为成功。**
3. **待复核：并发历史K线请求出现一次 `ProtocolError` 和一次结果与顺序基线不同，尚不能直接断言均由 FIFO 串配造成。**
4. **测试覆盖缺口：周末未获得真实 tick，Q6 的真实价格线格式仍没有样本。**
5. **协议分类风险：普通 `cmd=4` 查询响应也会调用 push 监听器；另观察到未分类的 `cmd=25` 无请求帧。**

应优先处理第 1 项。真实样本不支持“响应回显请求 prefix”的假设，不能直接以实现 prefix 匹配作为修复方案。

## 2. 本轮验证环境与前置变化

### 2.1 登录修复

当前工作区在登录 payload 末尾增加了 build 6320 观察到的额外 `PROP_U64` 字段：

- `pymt5/client.py:690` 附近
- `tests/test_protocol_smoke.py` 已将登录 payload 长度从 912 更新为 920，并检查末尾字段。

这是本轮验收前已经存在的用户工作区修改，应保留，不要在交接处理中回退。

### 2.2 integration 测试中的 symbol 返回契约

首次运行结果为 **4 passed、2 failed**。两个失败不是登录问题，而是 `tests/test_integration.py` 仍按旧契约假设 `load_symbols()` 返回 `list[dict]`；当前真实公开 API 返回 `dict[str, SymbolInfo]`：

- 实现：`pymt5/_market_data.py:128`
- 类型：`pymt5/types.py:71`

在用户要求停止修改前，工作区中的 integration 测试已做以下调整：

- 按 `dict[str, SymbolInfo]` 检查 symbol 结果；
- 从 `next(iter(symbols.values())).symbol_id` 取得订阅 ID；
- tick 测试在 `finally` 中退订。

调整后定向运行 `symbol_load or tick_subscription`：**1 passed、1 skipped、4 deselected**。`symbol_load` 通过；tick 用例因默认选中股票且周末 30 秒无 tick 而跳过，不是通过真实 tick 验证。

## 3. P0：已确认的同 command 迟到响应串配

### 3.1 脱敏实测过程

在一个已登录 demo 连接上，仅使用无副作用的 ping：

1. 将第一个 `cmd=51` ping 的客户端超时设为 `0.001s`，请求已发送，客户端按预期超时并移除其 pending future。
2. 立即恢复超时并发送第二个 ping。
3. 第一个 ping 的迟到响应到达时，第二个 ping 正在 pending。
4. 第一个响应被同 command FIFO 分给第二个请求，第二个 `ping()` 因而返回成功。
5. 第二个 ping 的真实响应随后到达，此时已没有 pending 请求。

脱敏观测序列：

| 顺序 | command | code | 到达时同 command pending 数 | prefix 匹配 |
|---:|---:|---:|---:|---|
| 1 | 51 | 0 | 1（实际是后一个请求） | false |
| 2 | 51 | 0 | 0 | false |

第一个请求结果为 `timeout`，第二个请求结果为 `success`，但第二个成功实际由第一个请求的迟到响应完成。**这已在真实 build 6320 上复现，不再只是理论风险。**

### 3.2 相关代码

- `pymt5/transport.py:208`：`_send_raw()` 按 command 登记 future。
- `pymt5/transport.py:226-240`：超时/取消后退休当前 future。
- `pymt5/transport.py:290` 附近：`_dispatch()` 从同 command 队列左侧取下一个未完成 future。
- `pymt5/protocol.py:84-90`：响应 prefix 被解析器丢弃。

### 3.3 影响

- 普通查询可能拿到另一请求的数据。
- 对交易命令而言，超时只代表客户端等待结束，不代表服务端未执行；迟到响应若错配，可能进一步破坏结果归属。
- 不得自动重试交易请求。重试前必须核对服务端 orders/deals/positions，且仍不能把查询核对解释成绝对证明。

### 3.4 处理建议

先补确定性的离线回归测试，再评审修复设计。可研究超时后的连接隔离、command quarantine、连接代际或其他防串配机制，但必须明确：

- 真实采样未支持请求 prefix 回显；不能假装存在可靠 request ID。
- 只删除超时的 future 不足以防止迟到帧完成下一个请求。
- 对无法可靠关联的帧，宁可报告未知/连接失效，也不能分配给可能不相关的交易请求。
- 保持“交易结果未知”语义，不能把防串配修复宣传为服务端执行结果已知。

## 4. P1：tick 退订非零响应被忽略

### 4.1 实测结果

同一轮深度验收中：

- `cmd=7` 订阅响应：`code=0`；
- `cmd=7` 退订响应：`code=1`；
- `unsubscribe_ticks()` 没有抛异常，测试阶段被记录为 `ok`，但这里的 `ok` 只表示客户端方法没有异常，**不代表服务端接受退订**。

### 4.2 代码问题

`pymt5/_market_data.py:385-401`：

- 调用 `transport.send_command(CMD_SUBSCRIBE_TICKS, payload)` 后未检查 `result.code`；
- 随后直接执行 `self._subscribed_ids = remaining`。

因此非零响应时，本地状态可能与服务端订阅状态不一致。订阅方法 `pymt5/_market_data.py:369-382` 也未检查响应码，应一起补测试和契约审查。

### 4.3 后续需要确认的协议细节

全部退订目前发送的是四字节计数零：`struct.pack("<I", 0)`。需要区分：

- 服务端是否要求真正的空 payload `b""`；
- 是否不允许空订阅；
- `code=1` 在该 build/会话中的具体含义；
- 部分退订和全部退订是否行为不同。

在取得证据前不要仅把 `code=1` 静默忽略，也不要未经测试同时改变 payload 和公开异常语义。应覆盖：非零响应抛错、本地状态不误更新、部分退订、全部退订、重连后的恢复行为。

## 5. P1 待复核：并发历史K线结果异常

### 5.1 当前证据

顺序请求 XAUUSD、EURUSD、US500、ABTC、AETH、USDJPY 的历史K线均成功。随后对 XAUUSD 和 EURUSD 并发调用 `get_rates()`：

- 一个任务得到 `ProtocolError`；
- 另一个任务成功，但返回 bars 与此前同参数顺序基线不同。

本轮 `cmd=11` 共记录 8 帧：7 个 `code=0`、1 个 `code=1`，所有已记录响应的 prefix 均未匹配请求 prefix。

### 5.2 结论边界

这属于**待确认异常**，目前不能断言：

- 一定是 FIFO 将两个响应互换；
- 一定是解析器问题；
- 一定是服务器限流或拒绝并发。

为保持“发现问题但不继续修改”的要求，准备好的固定历史区间复核脚本没有执行。建议后续使用固定的已结束历史区间，比较每个并发响应正文与两个独立顺序基线，分类为 `self / other / neither / error`，并分别记录响应码。这样可区分响应互换、服务端拒绝和非确定性数据。

## 6. P2：真实 tick 与价格线格式仍未验收

深度采样选择了 XAUUSD、EURUSD、US500、ABTC、AETH、USDJPY：

- 订阅请求本身返回 `code=0`；
- 45 秒内没有 `cmd=8` tick；
- `cmd=7` 初始 tick 快照帧数为 0；
- raw/typed tick 样本数均为 0；
- `price_checks=[]`，没有真实价格缩放结论。

当前日期为周末，所选品种可能休市；ABTC/AETH 也不能在未核实合约信息时当作 24/7 加密货币。不得把本地构造 tick 或零实时样本写成 Q6 实盘通过。

后续建议：

1. 在活跃交易时段采样，并先确认可用的 24/7 crypto 合约，而不是仅按名称子串猜测。
2. 用 `tick_price_mode="normalized"` 保存少量脱敏行情数值，不保存账户数据。
3. 对同一服务器的近期 bars 交叉比较 wire 值与 `wire / 10**digits`，判断 `normalized` 或 `scaled`。
4. 再检查默认 `auto` 是否与样本一致；在证据不足前继续把它描述为兼容启发式。
5. integration 测试应允许通过环境变量指定活跃测试 symbol，并将“休市无数据”与“协议通过”分开报告。

## 7. P2：响应/push 分类需要明确契约

### 7.1 普通 cmd=4 响应会进入 push 监听器

在一次普通 `get_positions_and_orders()` 查询中，注册的 `on_trade_update()` 回调被调用 **1 次**。响应到达时有对应 pending 请求，因此这不是无请求 push。

原因是 transport 在解析 future 后仍遍历同 command 的 listener。需要产品/协议层决定这是有意的“查询与推送统一通知”还是分类缺陷；若调用方把 listener 当作纯服务端主动 push，可能重复处理查询结果。

本次只记录回调次数，没有归档任何持仓或账户内容。没有通过交易制造真实 `cmd=4` push。

### 7.2 未分类 cmd=25

本轮观察到：

- `cmd=15`：2 个无 pending 帧；
- `cmd=17`：1 个无 pending 帧；
- `cmd=25`：5 个无 pending 帧。

前两者已有登录状态/symbol detail 相关处理；`cmd=25` 当前没有明确的公开命令语义。建议结合私有前端快照和受控只读样本确认其含义，不要根据编号猜测，也不要将其当作请求响应。

## 8. 已通过或已有正面证据的项目

- build 6320 bootstrap：通过。
- 登录/登出：通过。
- symbol 加载：通过，本轮取得 12,157 个 symbol 的缓存结果。
- ping 与 health check：通过。
- 顺序历史K线查询：本轮 6 个所选 symbol 均成功。
- 自有连接主动触发 EOF 后自动重连：`reconnect_count=1`，重新登录成功、transport READY，随后 ping 成功。
- 重连后 `tick_price_mode="normalized"` 配置保留。
- integration fixture 的 `try/finally` 清理生效，本轮未再出现此前的 pending task/event-loop-closed 警告。

本轮没有重新执行完整离线测试套件，因此不能把此前的 `1225 passed、6 skipped` 直接当作本次登录修复后最终工作树的新结果。交接修改完成后应重新运行完整门禁。

## 9. 脱敏帧汇总

本轮深度脚本记录 25 个非登录帧元数据，不记录正文和 prefix 原值：

| command | 帧数 | code 分布 | 无 pending 帧数 |
|---:|---:|---|---:|
| 0 | 2 | `0 × 2` | 0 |
| 4 | 1 | `0 × 1` | 0 |
| 7 | 2 | `0 × 1, 1 × 1` | 0 |
| 11 | 8 | `0 × 7, 1 × 1` | 0 |
| 15 | 2 | `0 × 2` | 2 |
| 17 | 1 | `0 × 1` | 1 |
| 25 | 5 | `0 × 5` | 5 |
| 34 | 1 | `0 × 1` | 0 |
| 51 | 3 | `0 × 3` | 1（迟到测试的第二个真实响应） |

所有记录帧的 `prefix_match` 均为 false。无 pending 的服务端 push 本身没有待匹配请求，不应把其 false 当作异常；关键是请求响应样本同样未支持 prefix 回显。

## 10. 复现材料与使用边界

本轮临时脚本位于：

- `/tmp/pymt5_demo_deep_acceptance.py`：已执行，产生本文的深层验收结果。
- `/tmp/pymt5_demo_followup.py`：仅准备，**未执行**；用于固定历史K线并发分类、退订 payload 和 crypto symbol 复核。

`/tmp` 文件不是仓库交付物，可能随时消失。接手人应把必要场景转成仓库内的可控离线回归测试和显式启用的只读 integration 测试，不要依赖临时文件。

运行 live 验收时必须：

- 从本地 `.env` 读取 `PYMT5_SERVER`、`PYMT5_LOGIN`、`PYMT5_PASSWORD`，不要写入源码或聊天；
- 设置 `PYMT5_INTEGRATION=1` 才启用 integration；
- 将 `PYMT5_LOG_LEVEL=CRITICAL` 用于采样，避免现有成功登录日志输出 login/session；
- 默认禁止交易命令；
- 报告只保留 command、code、计数、匹配布尔值和必要的脱敏行情样本。

## 11. 建议处理顺序与完成标准

1. **先修迟到响应串配**：有离线迟到帧回归测试，证明旧请求的响应不会完成新请求；公开保留交易结果未知语义。
2. **处理 cmd=7 响应码与全部退订格式**：用真实只读证据确定 payload，失败时不得伪装成功或破坏本地状态。
3. **复核并发 cmd=11**：固定历史区间并分类响应正文，确认是否为 FIFO 串配后再改解析或关联逻辑。
4. **明确响应/push 分发契约**：覆盖普通 cmd=4 响应、无请求 push 和未知 cmd=25。
5. **补活跃时段 tick 样本**：完成 Q6 真实线格式记录；休市 skip 不得算作通过。
6. **运行完整门禁**：离线测试、Ruff、mypy、Sphinx，以及单独启用的 demo 只读 integration；如实记录 skip 和 live 失败。

在上述事项完成前，不建议将 Phase 27/R07 或 Phase 28/R08 标为完整通过，也不建议把本轮 demo 结果作为发布验收通过。
