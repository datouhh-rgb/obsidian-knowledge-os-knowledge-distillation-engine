---
name: "apply-source-method-a4e20f8d-75dba888"
description: "Apply 买进与卖出的风险执行方法 to evidence-grounded tasks. Use when the user explicitly needs this method and supplies its required inputs. Do not use for unrelated requests, missing critical inputs, or claims beyond the bound source Evidence. On Windows PowerShell, read this UTF-8 SKILL.md with -Encoding UTF8."
---
# 买进与卖出的风险执行方法

> Skill ID: `61742ef4-9e61-4a9d-bf24-8986aac7b44f` · Version: `1.0.0` · Invocation fingerprint: `5765916015c7ad4b8d1d`

## 1. Skill定位

Apply 买进与卖出的风险执行方法 as an Evidence-bound decision procedure. `engine_policy`

## 2. 适用场景

- Use when the task matches the Method Cluster purpose and the required source facts are available. `engine_policy`

## 3. 输入要求

- Provide the decision target, current constraints, and the relevant observable facts. `engine_policy`
- Provide or confirm access to the bound source Evidence before execution. `engine_policy`

## 4. 判断流程

1. Confirm that the request matches the Method Cluster purpose and boundaries. `engine_policy`
2. Compare the Evidence-bound concept signals and identify agreement, conflict, and missing inputs. `engine_policy`
3. Stop for human review when Evidence conflicts or critical inputs remain missing. `engine_policy`

## 5. 执行动作

1. “我那位朋友是位经纪商，前一天晚上我曾经去他的营业厅。第二天他站在交易所里，脑子里焦虑地斗争着，拿不定主意到底做市场哪一边。他把所有有利的和不利的因素都过了一遍，然而实在没法分辨哪些是谣言，哪些是事实。没有可靠的新闻做根据。他一会儿觉得战争不可避免，一会儿差不多百分之百地说服自己战争完全不可能发生。他的困惑肯定让他觉得脑袋热得慌，所以他摘下礼帽擦擦前额的汗水。他弄不清楚到底该买还是该卖。 · Evidence: `ev-30d84734bcd3ffedb6e1aa16e8067842bd923f0686412c32df0851cde7ba4942`
2. 利文斯顿太太凝视着报价板上不断下跌的股票价格，哀叫起来：“哦，黑利先生！我不要亏掉我的500美元！我该怎么办呢？” · Evidence: `ev-72d15188387b0ffd98b56c30df38cc02db4af851bb602bcace999ff8eb57e235`
3. “根本不是。我已经发现了一条容易的道路，于是一直坚持这条道路。实际上，我没办法不挣钱。要是你想听，我打算告诉你我的秘密。是这样的：我从不在底部买进，而且我总是过早卖出。” · Evidence: `ev-ce476184cbce28d70d23c029e6ef09bc52aee99df4cb5af4d7a5a258ecdefc54`

## 6. 输出格式

Return the decision context, Evidence observations, actions, boundaries, and unresolved review items. `engine_policy`

## 7. 使用边界

- Do not generalize beyond the bound source books, conversion, and Evidence. `engine_policy`
- Keep the output in candidate status until explicit human review is complete. `engine_policy`

## 8. 禁止场景

- Do not use when fewer than three valid Evidence records support the Method Cluster. `engine_policy`
- Do not auto-approve, auto-expose, or connect the candidate to a project. `engine_policy`

## 9. 原文证据

| Evidence | Source | Conversion | Locator | Excerpt |
|---|---|---|---|---|
| `ev-30d84734bcd3ffedb6e1aa16e8067842bd923f0686412c32df0851cde7ba4942` | `75dba888e796b4aae0ef85f99798b6c8eab002e2c2b213e98b9b3e5d5c32b828` | `d563d127872852958eb4b6e311258407b5865229e0eb52342963c113ad0a25e3` | `lines:2569-2569` | “我那位朋友是位经纪商，前一天晚上我曾经去他的营业厅。第二天他站在交易所里，脑子里焦虑地斗争着，拿不定主意到底做市场哪一边。他把所有有利的和不利的因素都过了一遍，然而实在没法分辨哪些是谣言，哪些是事实。没有可靠的新闻做根据。他一会儿觉得战争不可避免，一会儿差不多百分之百地说服自己战争完全不可能发生。他的困惑肯定让他觉得脑袋热得慌，所以他摘下礼帽擦擦前额的汗水。他弄不清楚到底该买还是该卖。 |
| `ev-72d15188387b0ffd98b56c30df38cc02db4af851bb602bcace999ff8eb57e235` | `75dba888e796b4aae0ef85f99798b6c8eab002e2c2b213e98b9b3e5d5c32b828` | `d563d127872852958eb4b6e311258407b5865229e0eb52342963c113ad0a25e3` | `lines:2499-2499` | 利文斯顿太太凝视着报价板上不断下跌的股票价格，哀叫起来：“哦，黑利先生！我不要亏掉我的500美元！我该怎么办呢？” |
| `ev-ce476184cbce28d70d23c029e6ef09bc52aee99df4cb5af4d7a5a258ecdefc54` | `75dba888e796b4aae0ef85f99798b6c8eab002e2c2b213e98b9b3e5d5c32b828` | `d563d127872852958eb4b6e311258407b5865229e0eb52342963c113ad0a25e3` | `lines:2609-2609` | “根本不是。我已经发现了一条容易的道路，于是一直坚持这条道路。实际上，我没办法不挣钱。要是你想听，我打算告诉你我的秘密。是这样的：我从不在底部买进，而且我总是过早卖出。” |

## 来源入口

- [[06_Sources/Books/股票大作手回忆录--75dba888/conversions/d563d127872852958eb4b6e311258407b5865229e0eb52342963c113ad0a25e3/BOOK_OVERVIEW|股票大作手回忆录]]
- [[06_Sources/Books/股票大作手回忆录--75dba888/originals/75dba888e796--股票大作手回忆录.azw3|原件]]
- Method: [[05_Knowledge/Methods/买进与卖出的风险执行方法--5d8b41b0|买进与卖出的风险执行方法]]

Engine policy 陈述只用于执行安全与格式控制，已明确标注，不代表原文观点。
