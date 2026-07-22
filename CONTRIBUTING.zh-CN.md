# 为 nanobot 贡献代码

感谢你来到这里。

nanobot 基于一个简单的信念而构建：优秀的工具应当让人感到从容、清晰且人性化。
我们深切关注有用的功能，但也坚信以少胜多：解决方案应当强大而不臃肿，有抱负而
不至于无谓地复杂。

本指南不仅关乎如何提交 PR，还关乎我们希望如何共同构建软件：用心、清晰，并
尊重下一位阅读代码的人。

## 维护者

维护者是帮助审查、组织和维护项目的社区管家。下表描述了每位维护者当前在开源项目中的职责。

| 维护者 | 角色 |
|------------|------|
| [@re-bin](https://github.com/re-bin) | 项目负责人；审查社区 PR 并处理合并 |
| [@chengyongru](https://github.com/chengyongru) | 审查社区 PR 并可予以批准；合并由项目负责人处理 |

## 贡献流程

### 我应该为何提交 PR？

欢迎就以下方面提交 PR：

- 新功能或特性
- 不改变行为的 bug 修复
- 文档改进
- 不影响功能的细微调整
- 范围明确且易于审查的重构
- 对 API 或配置的更改，且影响已记录在案

对于风险较高或较大的更改，请尽早提交 issue 或草稿 PR，以便在实现规模过大之前
讨论工作的形态。

### 开始工作

在进行更改之前，同步你的本地检出并创建一个主题分支。

```bash
git fetch upstream
git switch main
git pull --ff-only upstream main
git switch -c your-topic-branch
```

如果你的检出使用了不同的远程仓库名，请用你的主 HKUDS/nanobot 远程仓库代替
`upstream`。

将与主题无关的本地更改排除在主题分支之外。如果你的检出已有正在进行的工作，
请使用单独的 worktree，或在开始新分支之前完成该工作。

## 开发环境搭建

让搭建过程保持简单可靠。目标是让你快速进入代码：

```bash
# Clone the repository
git clone https://github.com/HKUDS/nanobot.git
cd nanobot

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint code
ruff check nanobot/

# Format code - optional. The existing tree predates `ruff format`,
# so running it broadly produces large unrelated diffs.
# Do not mix mechanical formatting churn into a functional PR.
# Use formatting only for the exact code your change intentionally touches.
ruff format <files-you-changed>
```

## 贡献许可

提交贡献即表示你确认有权提交，并同意该贡献将在项目的 MIT License 下授权。

## 代码风格

我们关注的不仅是通过 lint 检查。我们希望 nanobot 保持小巧、从容且可读。

在贡献代码时，请力求代码具有以下特质：

- 简单（Simple）：优先选择能解决真实问题的最小更改
- 清晰（Clear）：为下一位读者优化，而非追求巧妙
- 解耦（Decoupled）：保持边界清晰，避免不必要的新抽象
- 诚实（Honest）：不隐藏复杂性，但也不制造额外的复杂性
- 持久（Durable）：选择易于维护、测试和扩展的解决方案

实践要点：

- 行长度：100 字符（`ruff`）
- 目标版本：Python 3.11+
- 代码检查：`ruff`，规则 E、F、I、N、W（忽略 E501）
- 异步：全程使用 `asyncio`；pytest 使用 `asyncio_mode = "auto"`
- 优先选择可读的代码，而非花哨的代码
- 优先选择聚焦的补丁，而非大范围重写
- 不要将机械性的格式化、换行、导入排序或引号变更混入功能或 bug 修复 PR。如需
  格式清理，请单独提交一个仅含格式化的 PR。
- 如果引入新抽象，它应当明显降低复杂性，而非仅仅转移复杂性

## 修改 CI 工作流

如果你的 PR 涉及 `.github/workflows/`，请将 CI 保持在 GitHub Actions 的免费
额度内：

- 仅使用标准的 GitHub 托管运行器（`ubuntu-latest`、`windows-latest`）
- 避免使用 macOS 运行器、大型运行器（`*-cores`、`*-xlarge`、`*-gpu`）以及
  自托管运行器
- 避免上传大型 artifact 或使用过长的保留期
- 避免使用付费的 Marketplace action

如果你的更改确实需要突破上述限制，请在 PR 描述中明确说明，以便在合并前进行
讨论。

## 有疑问？

如果你有疑问、想法或尚不成熟的见解，这里热烈欢迎你。

欢迎提交 [issue](https://github.com/HKUDS/nanobot/issues)、加入社区，或直接
联系：

- [Discord](https://discord.gg/MnCvHqpUGB)
- [飞书/微信](./COMMUNICATION.md)
- 邮箱：Xubin Ren（@Re-bin）- <xubinrencs@gmail.com>

感谢你为 nanobot 投入的时间与用心。我们期待更多人参与这个社区，并真诚欢迎
各种规模的贡献。
