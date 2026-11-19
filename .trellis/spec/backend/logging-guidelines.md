# 后端日志规范

## 适用范围

FastAPI 后端统一使用 Python 标准库 `logging`。请求日志在 `app/main.py`
中安装，并通过 `extra` 映射输出结构化字段。应用级日志统一使用
`ashare_ledger.*` 命名空间。

## 日志等级

- `INFO`：请求完成和重要状态变更。
- `DEBUG`：显式启用后输出诊断细节。
- `WARNING`：可恢复的行情供应商降级或其他非致命异常。
- `ERROR`：请求失败或未预期异常，应同时保留异常堆栈。
- `CRITICAL`：应用无法继续运行时使用。

## 必须记录的字段

请求日志必须包含 `request_id`、`path`、`status_code` 和 `duration_ms`。
行情降级日志应标明供应商和缓存状态，但不得记录请求体、密码、令牌或其他凭据。

## 禁止记录的内容

不得记录密码、令牌、完整请求体或任意账户数据。只有在诊断确定性账务或行情
计算问题时，才允许记录金额和证券代码。

## 场景：通过 `LOG_LEVEL` 控制后端日志等级

### 1. 范围与触发条件

- 触发条件：新增或修改后端日志初始化、日志等级相关环境变量、Uvicorn 日志接线或日志过滤行为。
- 该配置必须同时覆盖 `ashare_ledger.*`、`uvicorn`、`uvicorn.error` 和 `uvicorn.access`。

### 2. 签名

- `normalize_log_level(value: str) -> str`：校验并返回大写标准等级名称。
- `Settings.log_level: str = "INFO"`：保存经过校验的应用日志等级。
- `configure_logging(log_level: str) -> None`：应用根记录器和 Uvicorn 命名记录器的等级。
- 环境变量：`LOG_LEVEL`，可选，默认 `INFO`。

### 3. 契约

- `LOG_LEVEL` 不区分大小写，只接受 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。
- 配置对象必须将合法值规范为大写，并让非法值在应用启动前失败。
- `configure_logging` 必须设置应用日志命名空间及 Uvicorn 服务、错误、访问记录器的等级。
- 重复调用不得新增重复的根记录器或 Uvicorn Handler。
- Docker Compose 必须把宿主环境的 `LOG_LEVEL` 传入后端容器。
- 未设置 `LOG_LEVEL` 时使用 `INFO`，保留请求完成日志的既有行为。

### 4. 校验与错误矩阵

| 条件 | 必须结果 |
|---|---|
| 未设置 `LOG_LEVEL` | 使用 `INFO` |
| 值为 `debug`、`Warning` 等大小写变体 | 规范为对应大写等级 |
| 值为空字符串或未知名称 | 抛出包含 `LOG_LEVEL` 的 `ValueError` |
| 值为 `WARNING` | 过滤应用与访问日志中的 INFO 记录 |
| 重复调用 `configure_logging` | 不新增重复 Handler |

### 5. 正常、基础与错误案例

- 正常：`LOG_LEVEL=DEBUG` 时，应用与 Uvicorn 命名记录器均使用 `DEBUG`。
- 基础：未配置 `LOG_LEVEL` 时保持 `INFO` 请求日志。
- 错误：`LOG_LEVEL=verbose` 时必须在启动配置阶段报错，不能静默回退。

### 6. 必须的测试

- 断言默认值为 `INFO`。
- 参数化断言大小写变体规范化为大写等级。
- 断言非法值抛出包含 `LOG_LEVEL` 的 `ValueError`。
- 断言 `WARNING` 过滤应用日志与 `uvicorn.access` 的 INFO，同时保留 WARNING。
- 断言所有约定记录器得到目标等级。
- 断言重复配置不会新增根记录器或 Uvicorn 访问 Handler。

### 7. 错误与正确写法

#### 错误

```python
logging.getLogger("ashare_ledger").setLevel(os.getenv("LOG_LEVEL", "INFO"))
```

未知等级会被静默接受，且 Uvicorn 日志不受控制。

#### 正确

```python
normalized_level = normalize_log_level(settings.log_level)
configure_logging(normalized_level)
```

配置在启动前完成校验，并统一覆盖应用与 Uvicorn 日志。
