---
name: image-generation
description: 生成图像并迭代编辑已保存的图像 artifact。
---

# 图像生成

当用户要求你创建、渲染、绘制、设计、生成或编辑图像时，使用 `generate_image` 工具。

如果当前工具列表中没有 `generate_image` 工具，请告诉用户此 nanobot 实例未启用图像生成。

## 何时使用

- 文本生成图像：使用具体的 `prompt` 调用 `generate_image`。
- 图像编辑：在 `reference_images` 中传入已保存的 artifact 路径或用户图像路径。
- 同一对话中的迭代编辑：如果用户说"让它更亮些"、"更改背景"或"再试一个版本"之类的话，优先使用最近生成的图像 artifact。
- 模糊的编辑：如果多张最近的图像都可能成为目标，提出简短的澄清问题。
- 生成图像后，调用 `message` 工具并在 `media` 参数中传入 artifact 路径，将其交付给用户。

## Prompt 规则

编写具有足够细节的 prompt 供图像模型使用：

- 主体和场景。
- 构图和镜头或布局。
- 风格、氛围、光照和调色板。
- 必须出现在图像中的文本，需精确引用。
- 约束条件，例如"保持相同角色"、"保留 logo"或"不要更改背景"。

## Artifact 规则

该工具将生成的图像作为持久 artifact 存储在 nanobot 的 media 目录下，并返回结构化的元数据：

- `id`：生成的图像 id，例如 `img_ab12cd34ef56`。
- `path`：用于内部后续编辑的本地文件路径。
- `mime`：图像 MIME 类型。
- `prompt`、`model` 和 `source_images`：用于后续编辑的来源信息。

在正常的面向用户的回复中，不要暴露本地文件系统路径。保持回复自然，例如"完成，我已生成它。"当有助于用户引用特定图像时，可以包含简短的图像 `id`，但除非用户明确要求调试细节或本地 artifact 引用，否则将原始 `path` 保持在内部。绝不要粘贴 base64。

对于后续编辑，将先前的 artifact `path` 传给 `reference_images`。如果用户提供了新上传的图像，则改用该路径作为参考。

不要在面向用户的回复中包含内部回放标记，例如 `[Message Time: ...]`、`[image: /local/path]`、`generate_image(...)` 或 `message(...)`。

## 示例

生成新图像：

```text
generate_image(
  prompt="A minimal app icon for nanobot: friendly robot head, rounded square, soft blue and white palette, clean vector style, no text",
  aspect_ratio="1:1",
  image_size="1K"
)
```

编辑最新生成的 artifact：

```text
generate_image(
  prompt="Use the reference image. Keep the same robot and composition, but change the palette to warm orange and add a subtle sunrise background.",
  reference_images=["/home/user/.nanobot/media/generated/2026-05-08/img_ab12cd34ef56.png"],
  aspect_ratio="1:1",
  image_size="1K"
)
```
