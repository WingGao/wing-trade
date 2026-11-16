# A 股行情源调研

调研日期：2026 年 11 月 16 日。本文仅记录调研，尚未修改产品代码。

## 一手依据

- 现有可选适配器 `backend/app/market_data/provider.py:134-180` 调用 `akshare.stock_zh_a_spot_em()`，返回的数值直接进入严格 Decimal 校验，目前还把获取时间当作报价时间。
- 检查过的 AKShare 官方源码提交为 `c6d3705ef9f1e0cc2d5ed7ab259c18d7cbb15e8b`：
  `https://github.com/akfamily/akshare/blob/c6d3705ef9f1e0cc2d5ed7ab259c18d7cbb15e8b/akshare/stock/quote/stock_hist_em.py`
- 该函数获取全市场快照，并通过 pandas 转换数值。官方分页辅助函数在后续页面之间等待，不适合个人持仓每 10 秒刷新的默认路径：
  `https://github.com/akfamily/akshare/blob/main/akshare/utils/func.py`
- AKShare 的 HTTP 封装记录了 push2 限流及避免立即重复重试的说明：
  `https://github.com/akfamily/akshare/blob/main/akshare/utils/http.py`
- 2026 年 11 月 16 日，对 SH:600519、SZ:002594 的只读实际接口探测成功：
  `https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=1.600519,0.002594&fields=f12,f13,f14,f2,f3,f18,f124`
  返回两条对应证券记录，包含最新价、昨收、当日涨幅和报价时间戳。一次成功请求不代表服务等级承诺，也不代表已验证全部市场。
- AKShare 文档首页可访问，但抓到的 HTML 没有暴露所检索的函数章节。PyPI 元数据查询超时，没有据此推断包版本。

## 技术选择

在现有 QuoteProvider 协议下实现 EastmoneyQuoteProvider，通过有请求边界的标准库 HTTP／JSON 访问批量接口，避免全市场扫描，也无需添加 AKShare／pandas 运行时依赖。保留现有可选 AKShare 实现，不将本任务扩大为 SDK 重写。

## 传输与字段映射

- 使用固定 HTTPS 行情接口，不涉及券商凭据、浏览器登录或下单接口。
- 只请求既有大写 SH/SZ/BJ 市场加六位代码。SH 映射到上游市场 1，SZ/BJ 映射到市场 0；返回行须同时匹配代码及市场。实际探测只验证 SH/SZ；BJ 标识和缺失行为用受控数据测试，真实连通性覆盖情况须如实报告。
- 仅批量请求所需的、支持的持仓／自选证券，每次最多 100 只。支持范围为空或只有美股时，不调用上游。
- 字段对应：f12 为代码，f13 为市场，f2 为最新价，f18 为昨收，f3 为按百分数单位表示的当日涨幅，f124 为报价时刻的 Unix 时间戳。
- 通过 `json.loads(..., parse_float=Decimal)` 解析数值，校验价格和可选昨收为有限正数。无效行或缺失占位符逐行跳过；只有昨收有效时才允许补算缺失涨幅。
- 有效报价时间戳转为带时区 UTC；缺失或无效时保留 null。成功收到数据的时刻是获取时间，不能伪装为市场更新时间。
- 网络超时默认 5 秒，同时限制响应大小及多批请求的累计工作量。同步 HTTP 工作线程结束前，采集器持续持有单次在途刷新边界。所有数据库写入均在采集器／请求会话内，HTTP 工作线程不操作数据库。
- 不立即密集重试，也不轮换主机规避限流。复用共享熔断退避。缺失／无效证券保留旧缓存并标为过期。

## 限制与验证

该接口是公开网站数据接口，不是签约交易所行情。字段、访问方式、延迟和可用性可能变化，必须显示来源和过期状态，并保持行情提供者可替换。

自动化测试注入 HTTP 传输、行情提供者和时钟，覆盖精确数值解析、代码／市场映射、无效或缺失数据、时间戳、不支持／空范围、超时、批量请求和过期回退。部署时可单独检查实时连通性，但不能替代确定性测试。
