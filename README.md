# GitHub Idea Finder

从一个产品想法出发，发现并评估 GitHub 上的开源替代项目。

这个 skill 面向“我想做一个类似某产品的开源版本”或“有没有现成开源项目可以复用”的场景。它不会只执行一次关键词搜索，而是由 Agent 规划多轮查询、检查 README、补充搜索词，再对候选项目进行证据化比较。

## 能做什么

- 将自然语言产品想法整理成 Search Brief
- 按产品类别、产品形态和可观察能力生成多组搜索路线
- 使用 GitHub API 搜索并去重仓库
- 读取仓库元数据和 README
- 从 README 中发现相关项目和新的社区术语
- 根据 README 证据判断 GUI、独立运行、工具调用、多步骤任务等能力
- 区分完整产品、客户端/管理层、Agent runtime、组件、基础设施和资源列表
- 输出 `adopt`、`pilot` 或 `watch` 建议，以及缺口和维护信号
- 把跨会话沉淀的搜索与判断经验写回经验文件，让 skill 越用越好用

它不承诺穷尽 GitHub，也不把 Stars 当作产品匹配度。

## 核心流程

```text
产品想法
   ↓
Search Brief
   ↓
多路线搜索（高召回）
   ↓
候选去重与分组
   ↓
README / 元数据检查
   ↓
从证据生成新术语和下一轮查询
   ↓
能力匹配、缺口和产品形态判断
   ↓
候选对比报告
```

发现和判断是两个阶段：搜索阶段尽量保留候选，判断阶段才依据 README 和项目形态做语义筛选。项目名称中出现 `skill`、`workflow`、`MCP` 等词不会直接导致项目被丢弃；最终是否匹配由 Search Brief 和证据决定。

## 经验沉淀：越用越好用

每次会话结束后，可以把值得长期记住的经验写进一个跨会话的 Markdown 经验文件（默认 `work/github-idea-experience.md`），例如哪些查询词命中率高、哪些判断标准有效、踩过哪些坑。下次新会话开始前，先读取经验摘要，用它初始化搜索策略和判断口径，相当于「站在之前会话的肩膀上」而不是每次从零开始。

- 经验按领域分组（如 `ai-agent-desktop`、`server`），一条经验由 `key` 唯一标识；
- 相同 `key` 的新经验会**替换**旧经验，文件只会收敛、不会无限膨胀；
- 经验是「参考」而不是「硬约束」：Agent 仍以当前 README 证据为准，经验只是帮它更快出发。

## 目录结构

```text
github-idea-finder/
├── SKILL.md                    # Agent 使用说明
├── README.md                   # 本文档
├── .env.example                # 环境变量示例
├── agents/openai.yaml          # Skill 展示信息
└── scripts/
    ├── github_discovery.py     # 新版有状态、多轮搜索入口（含经验沉淀）
    └── test_github_discovery.py
```

## 环境要求

- Python 3.10 或更高版本
- 可访问 `api.github.com`
- 推荐配置 GitHub Personal Access Token

Token 只用于 GitHub API 认证。脚本会优先读取进程环境变量 `GITHUB_TOKEN`，然后读取当前工作目录或 skill 目录下的 `.env` 文件。

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
GITHUB_TOKEN=ghp_your_token_here
```

不要把真实 token 提交到 Git。仓库已通过 `.gitignore` 忽略 `.env`，仍应在提交前确认没有暂存凭据。

## 推荐用法

### 1. 创建搜索会话

会话文件保存查询账本、已发现仓库、已检查仓库和预算，建议放在工作目录而不是 skill 目录：

```powershell
python scripts/github_discovery.py session `
  --state-file work/github-idea-session.json `
  --idea "完整产品，类似 Codex App 的有 GUI、可独立运行的通用 AI Agent，支持工具调用和多步骤任务" `
  --experience-file work/github-idea-experience.md `
  --max-rounds 4 `
  --max-searches 30 `
  --max-inspections 50
```

`--experience-file` 指向跨会话经验文件（省略时默认 `work/github-idea-experience.md`）。会话开始时读取，结束时用 `learn` 写入。

### 0. 读取历史经验（可选，建议）

新会话开始前，先看经验摘要，把历史教训纳入本次的搜索计划和判断口径：

```powershell
python scripts/github_discovery.py experience --experience-file work/github-idea-experience.md
```

返回经验条数、按领域分布和经验内容全文。

### 2. 执行第一轮搜索

Agent 应根据 Search Brief 生成多组互补查询，而不是只搜索一个产品名：

```powershell
python scripts/github_discovery.py search `
  --state-file work/github-idea-session.json `
  --query "open source AI agent" `
  --query "desktop AI agent" `
  --query "self-hosted autonomous agent" `
  --query "AI agent tool execution" `
  --query "AI agent task planning" `
  --topic ai-agent `
  --topic ai-assistant `
  --fetch-limit 30
```

对于未带 GitHub qualifier 的查询，脚本会自动增加对应的 `in:readme` 路线；同一条查询不会在一个会话中重复执行。

### 3. 检查候选项目

候选选择应覆盖不同搜索路线和不同产品形态，不要只选择 Star 数最高的项目：

```powershell
python scripts/github_discovery.py inspect `
  --state-file work/github-idea-session.json `
  --repo owner/repo `
  --repo another-owner/another-repo
```

检查结果包括：

- 仓库元数据、License、归档状态和活跃度信号
- README 摘要；若 README 超过 6,000 个字符，结果会标记 `readme_truncated=true`，并保留完整 README 用于链接提取。README 请求失败会标记为 `readme_status=unavailable`，不会消耗检查次数，重试即可
- README 中链接到的 GitHub 仓库
- 后续搜索可以使用的新产品术语

### 4. 根据 README 证据继续搜索

检查 README 后，Agent 应从项目自己的词汇中生成下一轮查询，例如：

```text
open source cowork desktop in:readme
local-first AI agent workspace in:readme
computer-use agent desktop in:readme
agent harness desktop app in:readme
multi-agent workforce desktop in:readme
```

不要重复已经使用过的查询。达到轮次、搜索次数或检查次数预算后，或者领先候选已经有足够证据时即可停止。

### 5. 沉淀经验（会话结束后）

把值得长期记住的经验写回经验文件，让下次会话直接受益：

```powershell
python scripts/github_discovery.py learn `
  --experience-file work/github-idea-experience.md `
  --domain "ai-agent-desktop" `
  --key "which queries work well" `
  --body "桌面 AI Agent 检索命中：'open source AI agent'、'desktop AI agent'、'agent harness desktop app'，并各自补一条 in:readme 路线。"
```

- `--domain`：经验所属领域，作为 Markdown 分组标题；
- `--key`：经验短标签，用于去重——相同 key 会替换旧条目；
- `--body`：经验正文，Agent 用自然语言描述可复用的结论。

只沉淀**跨会话、可复用**的经验（查询族、判断标准、常见坑），不要写某个项目的细节。

## 结果判定

最终报告至少应说明以下内容：

| 维度 | 需要回答的问题 |
| --- | --- |
| 产品形态 | 是完整产品、客户端、管理层、runtime、组件还是基础设施？ |
| GUI | 是否有桌面 GUI、Web UI 或其他可操作界面？ |
| 独立运行 | 是否能单独部署和运行，还是必须依赖外部 Agent 或云服务？ |
| 工具调用 | README 是否明确说明工具、函数、MCP 或实际环境操作？ |
| 多步骤任务 | 是否有规划、逐步执行、并行 Agent、工作流或长任务证据？ |
| License | 是否存在明确且可接受的开源许可证？ |
| 维护信号 | 项目是否活跃、可安装、存在发布或持续开发迹象？ |
| 缺口 | 与 Search Brief 的关键差异是什么？ |

每项能力应标记为 `supported`、`not-supported` 或 `uncertain`，并附上 README 或 release 中的直接证据。单纯命中 topic、仓库描述或关键词不足以证明能力。

建议使用以下决策：

- `adopt`：产品形态匹配，必需能力有直接证据，适合优先试用或集成
- `pilot`：方向匹配但有关键能力、独立性或成熟度不确定，适合小范围验证
- `watch`：有相关性但证据不足、产品形态不匹配或缺少关键能力

## 运行测试

在 `scripts` 目录执行：

```powershell
python -m unittest discover -s scripts -p "test_*.py" -v
```

测试覆盖会话状态、查询路线、README 仓库链接提取、去重、预算控制和 `.env` token 读取。

## 设计原则

1. **搜索由 Agent 规划，检索由脚本执行。** Agent 负责理解需求和选择下一轮查询，脚本负责 API、状态、去重和预算。
2. **先高召回，再证据判断。** 搜索阶段不使用针对某个行业或项目的固定黑名单。
3. **能力别名动态生成。** 查询词、证据短语和歧义说明应从当前 Search Brief 及 README 中生成，不维护不断增长的竞品名称表。
4. **产品形态是证据，不是自动过滤器。** 一个项目可能不是完整产品，但仍可能是有价值的 Agent runtime 或 GUI 管理层；报告中应正确分类。
5. **匹配度和健康度分开。** Star、Fork、最近提交等只能作为维护信号，不能替代功能证据。
6. **明确不确定性。** README 没有证明的能力应标记为 `uncertain`，而不是根据项目宣传语推断为已支持。
7. **经验跨会话沉淀。** 每次会话把可复用的搜索与判断经验写回经验文件，新会话先读取再出发；经验是参考不是硬约束，判断仍以当前 README 证据为准。

## 已知限制

- GitHub 搜索 API 的排序和索引并不稳定，结果会随时间变化。
- README 证据可能过时、夸大或只描述实验性功能，重要候选应继续检查 release、安装文档和实际运行情况。
- `health_score` 是维护信号，不是产品匹配分数。
- 会话有明确预算，搜索结果不是 GitHub 全量枚举。
- Cindy、Hermes Desktop 等项目可能将客户端、后端服务和 Agent runtime 分布在不同仓库，必须在报告中说明依赖边界。

## 相关文件

- [SKILL.md](SKILL.md)：给 Agent 的完整工作流和报告要求
- [scripts/github_discovery.py](scripts/github_discovery.py)：搜索和检查实现
- [.env.example](.env.example)：环境变量示例
