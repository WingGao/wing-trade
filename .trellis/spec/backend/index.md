# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

This directory contains guidelines for backend development. Fill in each file with your project's specific conventions.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | Implemented |
| [Database Guidelines](./database-guidelines.md) | ORM patterns, queries, migrations | Implemented for M0 ledger |
| [Error Handling](./error-handling.md) | Error types, handling strategies | Implemented for M0 API |
| [Quality Guidelines](./quality-guidelines.md) | Code standards, forbidden patterns | Implemented for M0 ledger |
| [Logging Guidelines](./logging-guidelines.md) | Structured logging, log levels | Implemented |
| [Market Data Guidelines](./market-data-guidelines.md) | Quote provider, cache, staleness, circuit breaker | Implemented for M1 |
| [持仓批次与三级止盈契约](./portfolio-batch-contracts.md) | 等量配对、交易编辑与待刷新、派生重建、继承投影、外部名称和真实日内行情 | 已实现 |
| [企业微信通知渲染契约](./wecom-notifications.md) | 企微 Markdown 止盈/买回提醒红涨绿跌着色规则 | 已实现 |

---

## How to Fill These Guidelines

For each guideline file:

1. Document your project's **actual conventions** (not ideals)
2. Include **code examples** from your codebase
3. List **forbidden patterns** and why
4. Add **common mistakes** your team has made

The goal is to help AI assistants and new team members understand how YOUR project works.

---

**Language**: All documentation should be written in **English**.
