# super-harness

> 面向 spec 驱动 AI 编码工作流的、缺失的那层 CI。

[English](README.md) | **简体中文**

## super-harness 是什么?

一个开源、CI 优先、框架无关、agent 无关的 harness,让 AI 编码变得确定、可靠。
Spec 驱动的工具(如 Spec Kit、OpenSpec、Superpowers)用 markdown 描述规则,agent 读了之后(以概率)遵守;harness 则把这些
约束嵌进环境本身 —— hooks、CI、git、进程 —— 于是违规是被**确定性地拦下**,而不只是
被劝阻。它长在你现有的 spec 框架和 agent 之上,不替代其中任何一个。

关于它解决的问题、v0.1 交付了什么、以及跟邻近工具的关系,见
[Overview](docs/overview.md)。

## 安装

```bash
pipx install super-harness
brew install gh && gh auth login   # gh 是 init --setup-github 的前置依赖
```

## Quickstart

引导一个仓库,亲眼看门拦住一次"越出生命周期"的编辑 —— 这正是这个工具的意义所在:

```bash
pipx install super-harness
cd your-repo && super-harness init            # 创建 .harness/ 数据面
super-harness adapter install claude-code     # 接入你的 agent(若有 .claude/,init 会自动装)
#   Codex 用法:     super-harness adapter install codex   → 再在 Codex 里跑 /hooks 信任它
super-harness change start "my-change"        # → INTENT_DECLARED
# 现在让你的 agent(或你)去改代码 → 门会拦住,
# 因为还没经过 plan review。这一拦,就是产品本身。
```

这是"看见 super-harness 工作"的最短路径。完整流程 —— 装框架适配器、过 plan review、
实现、验证、评审、合并 —— 是 10 分钟的 [Getting started](docs/getting-started.md) 走查。
想不跑任何东西就看一个预置的非平凡 `.harness/` 状态,见仓内示例
[`examples/demo-openspec-claude/`](examples/demo-openspec-claude/)。

## 链接

- [文档索引](docs/README.md)
- [Overview](docs/overview.md) —— 它是什么、v0.1 交付了什么、邻近工具
- [Getting started](docs/getting-started.md) —— 完整端到端走查
- [Concepts](docs/concepts.md) —— 生命周期,以及 harness *不*替你做的事
- [Adopting](docs/adopting.md) —— 在你自己的项目里锁住架构规则
- [Limitations & FAQ](docs/limitations.md)
- [Agent 适配器](docs/adapters/) —— [Claude Code](docs/adapters/claude-code.md) · [Codex](docs/adapters/codex.md)(实验性)
- [CLI reference](docs/cli-reference.md)
- [Architecture](docs/ARCHITECTURE.md)

> 说明:深度文档(`docs/`)目前仅有英文;本页是面向中文读者的入口,链接指向同一份英文文档。

## License

MIT —— 见 [`LICENSE`](LICENSE)。
