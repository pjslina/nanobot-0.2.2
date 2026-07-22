---
name: skill-creator
description: 创建或更新 AgentSkills。在设计、组织或打包带有脚本、引用与资源的技能时使用。
---

# 技能创建器（Skill Creator）

本技能提供创建有效技能的指导。

## 关于技能（About Skills）

技能是模块化、自包含的包，通过提供专业知识、工作流和工具来扩展 agent 的能力。可以把它们看作特定领域或任务的"入职指南"——它们将 agent 从通用 agent 转变为具备程序化知识的专业 agent，而任何模型都无法完全具备这些知识。

### 技能提供什么

1. 专业化工作流 - 针对特定领域的多步骤流程
2. 工具集成 - 使用特定文件格式或 API 的说明
3. 领域专业知识 - 公司专属知识、schema、业务逻辑
4. 捆绑资源 - 用于复杂和重复任务的脚本、引用和资源

## 核心原则

### 简洁是关键

上下文窗口是公共资源。技能与 agent 需要的其他所有内容共享上下文窗口：系统提示、对话历史、其他技能的元数据，以及实际的用户请求。

**默认假设：agent 已经非常聪明。** 只添加 agent 尚未拥有的上下文。对每一条信息提出质疑："agent 真的需要这个解释吗？"以及"这一段是否对得起它的 token 成本？"

宁愿使用简洁示例，而非冗长的解释。

### 设置适当的自由度

将具体性程度与任务的脆弱性和可变性相匹配：

**高自由度（基于文本的指令）**：当多种方法都有效、决策依赖上下文、或由启发式方法指导时使用。

**中等自由度（伪代码或带参数的脚本）**：当存在首选模式、可接受一定变化、或配置影响行为时使用。

**低自由度（特定脚本、少量参数）**：当操作脆弱且易出错、一致性至关重要、或必须遵循特定顺序时使用。

把 agent 想象成在探索一条路径：悬崖边的窄桥需要具体护栏（低自由度），而开阔场地允许许多路线（高自由度）。

### 技能的结构

每个技能由一个必需的 SKILL.md 文件和可选的捆绑资源组成：

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter metadata (required)
│   │   ├── name: (required)
│   │   └── description: (required)
│   └── Markdown instructions (required)
└── Bundled Resources (optional)
    ├── scripts/          - Executable code (Python/Bash/etc.)
    ├── references/       - Documentation intended to be loaded into context as needed
    └── assets/           - Files used in output (templates, icons, fonts, etc.)
```

#### SKILL.md（必需）

每个 SKILL.md 由以下部分组成：

- **Frontmatter**（YAML）：包含 `name` 和 `description` 字段。这些是 agent 读取以确定何时使用技能的唯一字段，因此清晰、全面地描述技能是什么以及何时应使用它非常重要。
- **正文**（Markdown）：使用技能的说明和指导。只在技能触发后加载（如果会触发的话）。

#### 捆绑资源（可选）

##### 脚本（`scripts/`）

用于需要确定性可靠性或被反复重写的任务的可执行代码（Python/Bash 等）。

- **何时包含**：当相同代码被反复重写或需要确定性可靠性时
- **示例**：用于 PDF 旋转任务的 `scripts/rotate_pdf.py`
- **好处**：token 高效、确定性、可不加载到上下文中直接执行
- **注意**：脚本可能仍需被 agent 读取以便打补丁或进行环境特定调整

##### 引用（`references/`）

按需加载到上下文中的文档和参考材料，用于指导 agent 的流程和思考。

- **何时包含**：用于 agent 在工作时应参考的文档
- **示例**：用于财务 schema 的 `references/finance.md`、用于公司 NDA 模板的 `references/mnda.md`、用于公司政策的 `references/policies.md`、用于 API 规范的 `references/api_docs.md`
- **用例**：数据库 schema、API 文档、领域知识、公司政策、详细工作流指南
- **好处**：保持 SKILL.md 精简，只在 agent 确定需要时加载
- **最佳实践**：如果文件较大（>10k 词），在 SKILL.md 中包含 grep 模式以便 agent 高效使用内置搜索工具；说明何时默认 `grep(output_mode="files_with_matches")`、`grep(output_mode="count")`、`grep(fixed_strings=true)`，或通过 `head_limit` / `offset` 分页是正确的第一步
- **避免重复**：信息应只存在于 SKILL.md 或引用文件中，而非两者都有。除非确实是技能核心，否则优先使用引用文件存放详细信息——这样既保持 SKILL.md 精简，又能让信息可被发现而不占用上下文窗口。只在 SKILL.md 中保留必要的程序化指令和工作流指导；将详细参考材料、schema 和示例移至引用文件。

##### 资源（`assets/`）

不打算加载到上下文中、而是用于 agent 产出输出的文件。

- **何时包含**：当技能需要将在最终输出中使用的文件时
- **示例**：用于品牌资源的 `assets/logo.png`、用于 PowerPoint 模板的 `assets/slides.pptx`、用于 HTML/React 样板的 `assets/frontend-template/`、用于字体的 `assets/font.ttf`
- **用例**：模板、图像、图标、样板代码、字体、会被复制或修改的示例文档
- **好处**：将输出资源与文档分离，使 agent 能在不将文件加载到上下文的情况下使用它们

#### 不应包含在技能中的内容

技能应只包含直接支持其功能的必需文件。不要创建多余的文档或辅助文件，包括：

- README.md
- INSTALLATION_GUIDE.md
- QUICK_REFERENCE.md
- CHANGELOG.md
- 等

技能应只包含 AI agent 完成手头工作所需的信息。不应包含关于其创建过程的辅助上下文、设置和测试流程、面向用户的文档等。创建额外文档文件只会增加混乱。

### 渐进式披露设计原则

技能使用三级加载系统来高效管理上下文：

1. **元数据（name + description）** - 始终在上下文中（约 100 词）
2. **SKILL.md 正文** - 技能触发时（<5k 词）
3. **捆绑资源** - 由 agent 按需使用（无限制，因为脚本可在不读入上下文窗口的情况下执行）

#### 渐进式披露模式

保持 SKILL.md 正文精简且在 500 行以内，以减少上下文膨胀。接近此限制时将内容拆分到单独文件。将内容拆分到其他文件时，从 SKILL.md 引用它们并清晰描述何时读取非常重要，以确保技能读者知道它们的存在及使用时机。

**关键原则：** 当技能支持多种变体、框架或选项时，只在 SKILL.md 中保留核心工作流和选择指导。将变体特定细节（模式、示例、配置）移至单独的引用文件。

**模式 1：带引用的高级指南**

```markdown
# PDF Processing

## Quick start

Extract text with pdfplumber:
[code example]

## Advanced features

- **Form filling**: See [FORMS.md](FORMS.md) for complete guide
- **API reference**: See [REFERENCE.md](REFERENCE.md) for all methods
- **Examples**: See [EXAMPLES.md](EXAMPLES.md) for common patterns
```

agent 只在需要时加载 FORMS.md、REFERENCE.md 或 EXAMPLES.md。

**模式 2：按领域组织**

对于多领域技能，按领域组织内容以避免加载无关上下文：

```
bigquery-skill/
├── SKILL.md (overview and navigation)
└── reference/
    ├── finance.md (revenue, billing metrics)
    ├── sales.md (opportunities, pipeline)
    ├── product.md (API usage, features)
    └── marketing.md (campaigns, attribution)
```

当用户询问销售指标时，agent 只读取 sales.md。

类似地，对于支持多框架或变体的技能，按变体组织：

```
cloud-deploy/
├── SKILL.md (workflow + provider selection)
└── references/
    ├── aws.md (AWS deployment patterns)
    ├── gcp.md (GCP deployment patterns)
    └── azure.md (Azure deployment patterns)
```

当用户选择 AWS 时，agent 只读取 aws.md。

**模式 3：条件细节**

展示基本内容，链接到高级内容：

```markdown
# DOCX Processing

## Creating documents

Use docx-js for new documents. See [DOCX-JS.md](DOCX-JS.md).

## Editing documents

For simple edits, modify the XML directly.

**For tracked changes**: See [REDLINING.md](REDLINING.md)
**For OOXML details**: See [OOXML.md](OOXML.md)
```

agent 只在用户需要这些功能时读取 REDLINING.md 或 OOXML.md。

**重要指南：**

- **避免深层嵌套引用** - 保持引用距 SKILL.md 仅一层深度。所有引用文件应直接从 SKILL.md 链接。
- **为较长的引用文件构建结构** - 对于超过 100 行的文件，在顶部包含目录，以便 agent 在预览时能看到全部范围。

## 技能创建流程

技能创建包括以下步骤：

1. 通过具体示例理解技能
2. 规划可复用技能内容（脚本、引用、资源）
3. 初始化技能（运行 init_skill.py）
4. 编辑技能（实现资源并编写 SKILL.md）
5. 打包技能（运行 package_skill.py）
6. 基于实际使用迭代

按顺序执行这些步骤，仅在有明确原因不适用时才跳过。

### 技能命名

- 仅使用小写字母、数字和连字符；将用户提供的标题规范化为连字符形式（例如，"Plan Mode" -> `plan-mode`）。
- 生成名称时，生成少于 64 个字符的名称（字母、数字、连字符）。
- 优先使用描述动作的简短、动词引导的短语。
- 当能提升清晰度或触发效果时，按工具命名空间（例如，`gh-address-comments`、`linear-address-issue`）。
- 技能文件夹名称与技能名称完全一致。

### 步骤 1：通过具体示例理解技能

仅当技能的使用模式已被清楚理解时才跳过此步。即使在处理现有技能时，此步仍有价值。

要创建有效的技能，需清楚理解技能将如何被使用的具体示例。这种理解可来自直接的用户示例或经用户反馈验证的生成示例。

例如，在构建图像编辑器技能时，相关问题包括：

- "图像编辑器技能应支持什么功能？编辑、旋转、还是其他？"
- "你能举一些这个技能会如何被使用的例子吗？"
- "我能想象用户会要求诸如'去除这张图像的红眼'或'旋转这张图像'。你还能想到这个技能被使用的其他方式吗？"
- "用户说什么应该触发这个技能？"

为避免让用户感到负担，避免在单条消息中提出过多问题。从最重要的问题开始，根据需要跟进以提升效果。

当对技能应支持的功能有清晰认识时，结束此步。

### 步骤 2：规划可复用技能内容

要将具体示例转化为有效技能，通过以下方式分析每个示例：

1. 考虑如何从零开始执行该示例
2. 识别在反复执行这些工作流时哪些脚本、引用和资源会有帮助

示例：在构建 `pdf-editor` 技能以处理诸如"帮我旋转这个 PDF"的查询时，分析显示：

1. 旋转 PDF 每次都需要重写相同代码
2. 一个 `scripts/rotate_pdf.py` 脚本存储在技能中会有帮助

示例：在为诸如"给我构建一个 todo 应用"或"给我构建一个跟踪步数的仪表板"的查询设计 `frontend-webapp-builder` 技能时，分析显示：

1. 编写前端 webapp 每次都需要相同的 HTML/React 样板
2. 一个包含样板 HTML/React 项目文件的 `assets/hello-world/` 模板存储在技能中会有帮助

示例：在构建 `big-query` 技能以处理诸如"今天有多少用户登录？"的查询时，分析显示：

1. 查询 BigQuery 每次都需要重新发现表 schema 和关系
2. 一个记录表 schema 的 `references/schema.md` 文件存储在技能中会有帮助

为确立技能内容，分析每个具体示例以创建要包含的可复用资源列表：脚本、引用和资源。

### 步骤 3：初始化技能

此时，是实际创建技能的时候了。

仅当正在开发的技能已存在且需要迭代或打包时才跳过此步。此时继续下一步。

从零创建新技能时，始终运行 `init_skill.py` 脚本。该脚本便捷地生成一个新的模板技能目录，自动包含技能所需的一切，使技能创建过程更高效可靠。

对于 `nanobot`，自定义技能应位于活动工作区的 `skills/` 目录下，以便在运行时被自动发现（例如，`<workspace>/skills/my-skill/SKILL.md`）。

用法：

```bash
scripts/init_skill.py <skill-name> --path <output-directory> [--resources scripts,references,assets] [--examples]
```

示例：

```bash
scripts/init_skill.py my-skill --path ./workspace/skills
scripts/init_skill.py my-skill --path ./workspace/skills --resources scripts,references
scripts/init_skill.py my-skill --path ./workspace/skills --resources scripts --examples
```

该脚本：

- 在指定路径创建技能目录
- 生成带有正确 frontmatter 和 TODO 占位符的 SKILL.md 模板
- 可选地基于 `--resources` 创建资源目录
- 设置 `--examples` 时可选地添加示例文件

初始化后，按需自定义 SKILL.md 并添加资源。如果使用了 `--examples`，替换或删除占位符文件。

### 步骤 4：编辑技能

编辑（新生成或现有的）技能时，记住技能是为另一个 agent 实例创建使用的。包含对 agent 有益且非显而易见的信息。考虑哪些程序化知识、领域特定细节或可复用资源能帮助另一个 agent 实例更有效地执行这些任务。

#### 学习经验证的设计模式

根据技能需求查阅这些有用指南：

- **多步骤流程**：见 references/workflows.md 了解顺序工作流和条件逻辑
- **特定输出格式或质量标准**：见 references/output-patterns.md 了解模板和示例模式

这些文件包含有效技能设计的既定最佳实践。

#### 从可复用技能内容开始

要开始实现，从上面识别的可复用资源开始：`scripts/`、`references/` 和 `assets/` 文件。注意此步可能需要用户输入。例如，在实现 `brand-guidelines` 技能时，用户可能需要提供品牌资源或模板存入 `assets/`，或文档存入 `references/`。

添加的脚本必须通过实际运行来测试，以确保没有 bug 且输出符合预期。如果有许多类似脚本，只需测试代表性样本以确保它们都能工作，同时平衡完成时间。

如果使用了 `--examples`，删除技能不需要的占位符文件。只创建实际需要的资源目录。

#### 更新 SKILL.md

**编写指南：** 始终使用祈使/不定式形式。

##### Frontmatter

编写带 `name` 和 `description` 的 YAML frontmatter：

- `name`：技能名称
- `description`：这是技能的主要触发机制，帮助 agent 了解何时使用技能。
  - 包含技能做什么以及何时使用的具体触发器/上下文。
  - 将所有"何时使用"信息放在这里——而非正文中。正文只在触发后加载，因此正文中的"何时使用本技能"章节对 agent 没有帮助。
  - `docx` 技能的描述示例："Comprehensive document creation, editing, and analysis with support for tracked changes, comments, formatting preservation, and text extraction. Use when the agent needs to work with professional documents (.docx files) for: (1) Creating new documents, (2) Modifying or editing content, (3) Working with tracked changes, (4) Adding comments, or any other document tasks"

保持 frontmatter 精简。在 `nanobot` 中，需要时也支持 `metadata` 和 `always`，但除非确实需要，否则避免添加额外字段。

##### 正文

编写使用技能及其捆绑资源的说明。

### 步骤 5：打包技能

技能开发完成后，必须打包为可分发的 .skill 文件与用户共享。打包过程会先自动验证技能以确保满足所有要求：

```bash
scripts/package_skill.py <path/to/skill-folder>
```

可选输出目录指定：

```bash
scripts/package_skill.py <path/to/skill-folder> ./dist
```

打包脚本将：

1. **验证** 技能自动，检查：
   - YAML frontmatter 格式和必需字段
   - 技能命名约定和目录结构
   - 描述完整性和质量
   - 文件组织和资源引用

2. **打包** 技能（如验证通过），创建以技能命名的 .skill 文件（例如，`my-skill.skill`），包含所有文件并保持正确的目录结构以供分发。.skill 文件是扩展名为 .skill 的 zip 文件。

   安全限制：拒绝符号链接，当存在任何符号链接时打包失败。

如果验证失败，脚本将报告错误并退出而不创建包。修复任何验证错误并重新运行打包命令。

### 步骤 6：迭代

测试技能后，用户可能请求改进。这通常发生在使用技能之后不久，此时对技能表现有新鲜上下文。

**迭代工作流：**

1. 在真实任务上使用技能
2. 注意困难或低效之处
3. 识别 SKILL.md 或捆绑资源应如何更新
4. 实施变更并再次测试
