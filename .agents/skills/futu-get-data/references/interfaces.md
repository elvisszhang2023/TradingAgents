# 富途接口与返回字段

本参考对应 `tradingagents/dataflows/vendors/futu.py` 当前实现，只描述已接入的4个接口。

## 1. 资金流趋势 `capital-flow`

底层富途调用：

```python
context.get_capital_flow(
    code,
    period_type=PeriodType.DAY,
    start=start_date,
    end=end_date,
)
```

项目函数：

```python
get_capital_flow(symbol, start_date, end_date) -> str
```

返回带注释头的 CSV：

| 字段 | 含义 |
|---|---|
| `last_valid_time` | 富途返回的最后有效时间；某些市场可能为空 |
| `in_flow` | 当日总净流入 |
| `super_in_flow` | 超大单净流入 |
| `big_in_flow` | 大单净流入 |
| `mid_in_flow` | 中单净流入 |
| `sml_in_flow` | 小单净流入 |
| `main_in_flow` | 项目使用的主力口径，超大单与大单之和 |
| `capital_flow_item_time` | 数据所属交易日/时间 |

CSV 注释头还包含原代码、富途标准代码、市场、币种和请求窗口。金额单位是对应市场币种；正数为推断净流入，负数为推断净流出。

## 2. 当前资金分布 `capital-distribution`

底层富途调用：

```python
context.get_capital_distribution(code)
```

项目函数：

```python
get_capital_distribution(symbol, as_of_date=None) -> str
```

返回 JSON 顶层结构：

```json
{
  "source": "futu",
  "dataset": "live_capital_distribution",
  "symbol": "TSLA",
  "canonical_symbol": "US.TSLA",
  "market": "US",
  "currency": "USD",
  "semantics": "Order-size inferred inflow/outflow; not confirmed institutional trading.",
  "data": {}
}
```

`data` 字段：

| 字段 | 含义 |
|---|---|
| `capital_in_super` / `capital_out_super` | 超大单流入/流出 |
| `capital_in_big` / `capital_out_big` | 大单流入/流出 |
| `capital_in_mid` / `capital_out_mid` | 中单流入/流出 |
| `capital_in_small` / `capital_out_small` | 小单流入/流出 |
| `update_time` | 富途数据更新时间 |
| `net_super` / `net_big` / `net_mid` / `net_small` | 对应规模流入减流出，由项目计算 |
| `net_main` | `net_super + net_big`，由项目计算 |
| `net_total` | 四种规模净额之和，由项目计算 |

这是当前接口，不能用于过去某日的回测。订单规模分类不等于投资者身份。

## 3. 市场快照 `market-snapshot`

底层富途调用：

```python
context.get_market_snapshot([code])
```

项目函数：

```python
get_market_snapshot(symbol, as_of_date=None) -> str
```

返回 JSON 顶层结构与资金分布相似，`dataset` 为 `live_market_snapshot`。核心 `data` 字段：

### 通用字段

| 字段 | 含义 |
|---|---|
| `code`, `name`, `update_time` | 富途代码、名称和更新时间 |
| `sec_status`, `suspension`, `equity_valid` | 证券状态、是否停牌、正股字段是否有效 |
| `last_price`, `prev_close_price` | 最新价和前收盘价 |
| `open_price`, `high_price`, `low_price`, `avg_price` | 开高低和均价 |
| `volume`, `turnover` | 成交量和成交额 |
| `turnover_rate`, `volume_ratio`, `amplitude` | 换手率、量比和振幅 |
| `bid_price`, `ask_price`, `bid_vol`, `ask_vol` | 最优买卖价及对应数量 |
| `highest52weeks_price`, `lowest52weeks_price` | 52周最高和最低价 |
| `total_market_val` | 总市值 |
| `pe_ttm_ratio`, `pb_ratio`, `dividend_ratio_ttm` | TTM市盈率、市净率和TTM股息率 |
| `change_value` | `last_price - prev_close_price`，由项目计算 |
| `change_rate` | 相对前收盘价的百分比变化，由项目计算 |
| `position_52w` | 最新价位于52周低点到高点区间的百分比，由项目计算 |

### 港股附加字段

| 字段 | 含义 |
|---|---|
| `lot_size` | 每手股数 |
| `price_spread` | 最小价差 |

### 美股附加字段

| 字段 | 含义 |
|---|---|
| `pre_price`, `pre_change_rate`, `pre_volume` | 盘前价格、涨跌幅和成交量 |
| `after_price`, `after_change_rate`, `after_volume` | 盘后价格、涨跌幅和成交量 |
| `overnight_price`, `overnight_change_rate`, `overnight_volume` | 夜盘价格、涨跌幅和成交量 |

美股扩展时段字段可能因标的、当前交易阶段或行情权限为空。它们是时段汇总，不是逐分钟序列。

## 4. 历史日K `history-kline`

底层富途调用：

```python
context.request_history_kline(
    code,
    start=start_date,
    end=end_date,
    ktype=KLType.K_DAY,
    autype=AuType.QFQ,
    fields=[KL_FIELD.ALL],
    max_count=1000,
    page_req_key=page_key,
)
```

项目函数：

```python
get_history_kline(symbol, start_date, end_date) -> str
```

实现会自动翻页，按 `time_key` 去重和排序。返回带注释头的 CSV；注释头包含市场、币种、日期窗口和 `forward-adjusted (qfq)` 复权说明。

当前富途 `KL_FIELD.ALL` 通常返回以下字段，具体可用性以实际 CSV 表头为准：

| 字段 | 含义 |
|---|---|
| `code`, `name`, `time_key` | 代码、名称、K线时间 |
| `open`, `close`, `high`, `low` | 前复权开收高低价 |
| `pe_ratio` | 当日市盈率字段 |
| `turnover_rate` | 换手率 |
| `volume`, `turnover` | 成交量和成交额 |
| `change_rate` | 当日涨跌幅 |
| `last_close` | 前一交易日收盘价 |

该接口目前固定为日K，不返回分钟K、逐笔成交或完整盘前/盘后/夜盘K线。

## 公共错误和哨兵值

- `VendorNotConfiguredError`：`futu-api` 未安装或富途被关闭。
- `VendorUnavailableError`：OpenD 无法连接、SDK调用失败或富途返回非 `RET_OK`。
- `NoMarketDataError`：代码不受支持或富途返回空表。
- `FUTU_LIVE_DATA_WITHHELD`：历史分析请求了当前资金分布或市场快照。

接口失败时保留原始错误文本。不要把无权限、无覆盖、连接失败解释成股票没有成交或资金为零。
