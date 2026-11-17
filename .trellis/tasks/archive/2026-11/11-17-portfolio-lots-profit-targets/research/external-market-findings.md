# 外部证券与日内行情验证

2026-11-17 实际只读访问公开接口确认以下字段；自动测试全部注入固定传输，不依赖公网，不操作本地用户账本。

## 查名

- 端点：`https://searchapi.eastmoney.com/api/suggest/get`。
- 查询参数：`input`、`type=14`、既有公开 token、`count=20`。
- 响应：`QuotationCodeTable.Data[]`，证券名称 `Name`，代码 `Code`，行情市场 `MktNum`。
- `600519` 返回 `Name=贵州茅台`、`MktNum=1`；`AAPL` 返回 `Name=苹果`、`MktNum=105`、`QuoteID=105.AAPL`、`Classify=UsStock`。
- 查找只投影外部结果和本地 ID，不在读取时创建证券。

## 分钟时序

- 端点：`https://push2his.eastmoney.com/api/qt/stock/trends2/get`，参数 `secid`、`ndays=1`、`iscr=0`，显式请求 fields1/fields2。
- `fields2=f51,f52,f53,f54,f55,f56,f57,f58`，每条逗号字段第1项时间，第3项当前分钟收盘/最新价。
- 顶层 data 包含 `code`、`market`、`preClose`、`trends`。校验响应市场和代码，不接受串码。
- 沪股 `secid=1.600519`，深北 `0.CODE`；美股按外部查名得到的105/106/107市场。
- 关键时区证据：`105.AAPL` 响应从 `2026-11-16 22:30` 至 `2026-11-17 05:00`，是北京时间表达的纽约2026-11-16交易时段。解析先加上海时区，再换算纽约交易日；不能直接将字符串日期当美股交易日。
- 当天尚无行情时显示明确空状态，不把前一交易日曲线伪装成今日。交易时段分钟报价超过两分钟未更新标记过期。
- 有界同日缓存保留最后真实值；成功和失败请求都受最小间隔限制，跨交易日丢弃前日曲线，不重置历史报价时间冒充最新。
