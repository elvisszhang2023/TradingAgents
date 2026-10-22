---
name: futu-get-data
description: 当用户要求通过富途 OpenAPI 获取美股或港股的资金流、资金分布、行情快照或历史日K数据时触发。用于查询和解释富途原始数据，不负责完整股票研究、投资建议或交易下单。
---

# 富途行情数据获取

通过本项目已经封装的富途 OpenAPI 查询原始行情增强数据。只使用当前实现的4个接口：资金流、资金分布、市场快照和历史日K；不要自行扩展到下单、账户、逐笔成交、订单簿或实时K线。

## 前置条件

- 在本项目根目录执行命令。
- Futu OpenD 已启动并登录，默认监听 `127.0.0.1:11111`。
- 项目虚拟环境已安装 `futu-api`；使用 `.venv/bin/python`，不要依赖系统 Python。
- `FUTU_ENABLED` 默认为 `true`；可用 `FUTU_OPEND_HOST`、`FUTU_OPEND_PORT` 和 `FUTU_CONNECT_TIMEOUT_SECONDS` 覆盖连接设置。
- 可获取的市场、延迟级别和字段取决于富途账户的行情权限。

先检查 OpenD 端口：

```bash
nc -z 127.0.0.1 11111
```

端口不可用时，直接说明 OpenD 未连接并停止，不要用其他数据源伪装成富途结果。

## 标的代码

命令接受项目统一代码并自动转换：

- 美股：`TSLA` → `US.TSLA`
- 港股：`0700.HK` → `HK.00700`
- `9999.HK` → `HK.09999`

当前仅支持美股和港股。不要把 A 股、日股或加密货币代码传给本技能。

## 调用方式

使用技能自带脚本：

```bash
.venv/bin/python .agents/skills/futu-get-data/scripts/get_futu_data.py \
  --symbol TSLA \
  --api market-snapshot
```

可选接口：

- `capital-flow`：指定日期窗口的日级资金流趋势。
- `capital-distribution`：当前订单规模资金分布，只用于当前分析。
- `market-snapshot`：当前美股/港股行情、估值和成交快照。
- `history-kline`：前复权历史日K。
- `all`：并行需求不大时一次查询全部4个接口；输出一个包含各接口状态和原始文本的 JSON。

示例：

```bash
# 最近30个日历日资金流
.venv/bin/python .agents/skills/futu-get-data/scripts/get_futu_data.py \
  --symbol TSLA --api capital-flow --look-back-days 30

# 腾讯当前市场快照
.venv/bin/python .agents/skills/futu-get-data/scripts/get_futu_data.py \
  --symbol 0700.HK --api market-snapshot

# 最近120个日历日的前复权日K
.venv/bin/python .agents/skills/futu-get-data/scripts/get_futu_data.py \
  --symbol 0700.HK --api history-kline --look-back-days 120

# 截至指定日期的历史接口；实时接口会被明确标记为 withheld
.venv/bin/python .agents/skills/futu-get-data/scripts/get_futu_data.py \
  --symbol TSLA --api all --as-of-date 2026-10-20
```

`--as-of-date` 默认为当天，格式必须是 `YYYY-MM-DD`。`--look-back-days` 是日历日，不是交易日。资金流和历史K线也可以用 `--start-date` 与 `--end-date` 明确指定窗口；两者必须同时提供，并优先于回看天数。

## 接口选择和时效

- 查询一段时间的买卖资金趋势：使用 `capital-flow`。
- 查询当前超大单、大单、中单和小单的流入流出：使用 `capital-distribution`。
- 查询价格、成交、估值、52周位置，以及美股盘前/盘后/夜盘汇总：使用 `market-snapshot`。
- 查询价格与成交量的历史趋势：使用 `history-kline`。

`capital-distribution` 和 `market-snapshot` 是实时/当前接口。分析日期早于今天时，封装会返回 `FUTU_LIVE_DATA_WITHHELD`，防止未来数据泄漏。`capital-flow` 与 `history-kline` 可以指定历史窗口。

市场快照中的美股盘前、盘后和夜盘字段只是各时段的价格、涨跌幅和累计成交量汇总，不是连续24小时分钟K线。当前 `history-kline` 固定为前复权日K，也没有接入 `get_cur_kline`。

## 返回结果

单接口查询直接输出项目封装后的原始 JSON 或带元数据注释的 CSV。`all` 返回：

```json
{
  "symbol": "TSLA",
  "as_of_date": "2026-10-22",
  "results": {
    "market-snapshot": {
      "status": "success",
      "output": "{...原始JSON...}"
    }
  }
}
```

每个结果的 `status` 可能是：

- `success`：接口成功返回。
- `withheld`：历史分析禁止使用实时数据。
- `error`：连接、权限、代码或富途接口失败；同时包含 `error_type` 和 `error`。

字段定义、底层 SDK 调用及完整返回结构见 [references/interfaces.md](references/interfaces.md)。引用数据时必须同时说明标的、数据时间、市场和币种；不要把不同交易时段或不同币种的数据直接合并。

## 解释约束

- 富途的超大单/大单/中单/小单是按订单规模推断，不能确认高盛、基金或其他具体机构身份。
- 正数资金流表示推断净流入，负数表示推断净流出；不能单独作为买入建议。
- 实时快照与其他行情源可能因更新时间、复权、盘前盘后口径不同而不一致。保留差异并说明，不要擅自平均。
- `position_52w` 是当前价格在52周最低到最高之间的位置百分比，不代表估值便宜程度。
- 港股快照没有美股盘前、盘后和夜盘字段。

## 回复要求

成功时说明：

1. 实际调用的接口及参数。
2. 每个接口的成功、withheld 或失败状态。
3. 数据时间、市场、币种和关键字段。
4. 用户要求原始数据时，原样提供或保存返回内容；分析趋势时再做明确标注的计算，不覆盖原始值。

失败时返回富途原始错误、连接地址和失败接口，然后停止。不要切换到 Yahoo、FMP 或 Alpha Vantage，也不要在没有数据时推断资金方向。
