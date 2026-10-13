# Scientific Figure Reconstruction Skill

[English](README.md) · **简体中文**

**把科研示意图片变成可继续编辑的 PowerPoint 和 SVG。**

做论文插图、项目申请书或汇报 PPT 时，我们经常先用 AI 生成一张示意图，或者找到一张布局不错的参考图。整体效果可能已经合适，但想改一处术语、挪动一个节点、调整连接线，却发现手里只有一张 PNG/JPEG。

Scientific Figure Reconstruction Skill 旨在恢复有实际编辑价值的结构：将适合重建的文字、形状和连接关系转换为 **PowerPoint 与 SVG** 中的可编辑对象，复杂插图则保留为图片素材，让你能接着修改，而不必从头画起。

[效果展示](#效果展示) · [快速开始](#快速开始) · [可编辑范围](#输出与可编辑范围) · [开发计划](docs/roadmap.md)

## 效果展示

**AI 生成的科研示意图 → 可编辑 PowerPoint → SVG。**

这张图由 OpenAI 内置图像生成工具制作。下面的实验性输出复用真实 PNG-only Authoring Proposal，并经过一次本地诊断层级修复；当前公开版 Skill 不能自动复现同等效果。

[![AI-Generated Image、PowerPoint Reconstruction 与 SVG Reconstruction](assets/showcase/ai-comparison.png)](assets/showcase/ai-comparison.png)

*实验性重建：流程文字、节点和连接器可编辑，部分科研插图仍保留为图片素材。*

结果包含 **8 个原生流程节点和 9 条 PowerPoint 附着连接器**。字体、箭头和布局与原图仍有差异。保留图标是独立图片，不会自动随节点移动。[结果与验证说明](docs/showcase.md)。

[AI 生成的 PNG](examples/showcase-ai/original-ai-image.png) · [PowerPoint 草稿](examples/showcase-ai/unverified-draft.pptx) · [SVG 草稿](examples/showcase-ai/unverified-draft.svg)

### 在 PowerPoint 中继续编辑

![同一 AI 图重建结果的 PowerPoint 实际导出序列：改字、移动缩放、连接器跟随及保存重开](assets/showcase/ai-office-edit.gif)

这段 **PowerPoint 实际导出序列**来自同一张 AI 图的重建结果，展示已记录的文字修改、节点移动缩放及保存重开。[编辑范围](docs/showcase.md)。

此前的程序生成 UAV 图保留为[独立示例](examples/showcase-uav/README.md)。

## 主要功能

- **原生文字与规则形状**：把适合重建的标签、流程框和容器恢复为可编辑对象。
- **附着连接器**：为已识别的 PowerPoint 节点建立连接关系，方便调整布局。
- **按内容选择表示方式**：支持的结构采用原生或矢量元素，复杂科学区域保留为独立 Raster 素材。
- **PowerPoint / SVG 输出**：继续修改文字、对象几何和布局。
- **科学内容检查**：保留术语、数值和关系方向；对不确定的内容优先保留，并说明需要复查的部分。

## 快速开始

### 运行环境

准备 **Windows x64、Python 3.12，以及支持本地 Skill 的已登录 Codex 桌面端或 CLI**。实际桌面渲染和 PowerPoint 编辑还需要本机安装 **PowerPoint 16、Edge、Windows PowerShell**，以及 Arial 等所需字体。

默认采用 **Codex Host-Managed Skill**，使用已登录账户及其适用额度，**无需单独配置 OpenAI API Key**。[独立 API 模式](docs/optional-api.md)为可选功能。

### 1. 安装

在仓库根目录打开 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --isolated --index-url https://pypi.org/simple --only-binary=:all: --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --isolated --index-url https://pypi.org/simple --only-binary=:all: --require-hashes -r requirements-build.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation .
.\.venv\Scripts\python.exe -m pip check
```

### 2. 在 Codex 中使用

使用 Codex 打开仓库，或者运行 `codex -C <repository-root>`。Skill 位于 [`.agents/skills/scientific-figure-reconstruction/SKILL.md`](.agents/skills/scientific-figure-reconstruction/SKILL.md)；CLI 可通过 `/skills` 查找，也可以在对话中直接调用。

将明确允许处理的 PNG/JPEG 放到 `inputs/figure.png`，然后提出：

```text
使用 $scientific-figure-reconstruction 处理 inputs/figure.png。
保留原图中的文字和连接关系，使用新的输出目录，生成可编辑 PPTX/SVG。
说明哪些部分保留为图片素材，并在输出后停下，等待独立桌面渲染。
```

### 3. 生成和查看结果

Skill 准备图片、完成结构化 Authoring，再生成两种输出。需要查看实际渲染时，另行在正常登录、非管理员的 Windows 桌面终端执行。

<details>
<summary>本地准备与渲染命令</summary>

```powershell
.\.venv\Scripts\ufigure.exe prepare --workspace . --image inputs/figure.png --out runs/p
# Codex 查看 runs/p/input.png，写入 runs/p/proposal.json。
.\.venv\Scripts\ufigure.exe serialize --workspace . --image runs/p/input.png --proposal runs/p/proposal.json --out runs/i
# 在模型沙箱之外的受信任 Windows 桌面终端另行执行：
.\.venv\Scripts\ufigure.exe desktop-render --workspace . --artifacts runs/i --out runs/d
```

每次运行使用新的输出目录。

</details>

PPTX/SVG 和结构化结果位于 `runs/i/`，实际渲染与对照位于 `runs/d/desktop/`。[完整宿主使用流程](docs/host-managed.md)。

Authoring 使用 Codex 服务，可能传输图片及宿主上下文。工具、文件权限和沙箱由 Codex 管理；桌面渲染是独立的本地步骤。[安全与隐私说明](SECURITY.md)。

### 离线 Smoke Test

三节点 **Acquire → Validate → Report** 使用随附的示例 Proposal 检查本地流程，不调用模型。

```powershell
.\.venv\Scripts\ufigure.exe serialize --workspace . --image examples/workflow.png --proposal examples/proposal.json --out runs/smoke
# 本机具备 PowerPoint 和 Edge 时，由用户另行渲染：
.\.venv\Scripts\ufigure.exe desktop-render --workspace . --artifacts runs/smoke --out runs/smoke-render
```

[最小例与验证说明](docs/verification.md)。

## 输出与可编辑范围

| 内容 | 编辑方式 |
|---|---|
| PowerPoint 原生文字 | 修改标签、字体和文字几何 |
| 流程节点与规则容器 | 移动和缩放对象；已测试的标签随容器移动 |
| PowerPoint 附着连接器 | 端点跟随已连接节点，包括已测试的保存重开流程 |
| SVG 文字与矢量几何 | 修改文字和路径；连接线为静态路径，没有 Office 式动态附着 |
| 保留的图片素材 | 整体移动和缩放，内部内容仍为像素 |

不同图片的可编辑程度会有所不同。复杂科学插图与图表数据可能仍是 Raster；连接器跟随节点不等于自动解决布局碰撞。

## 工作原理

```text
PNG/JPEG
   ↓
Codex 视觉 Authoring → 结构化 Proposal
   ↓
CanonicalFigure → 校验与表示选择
   ↓
PPTX / SVG Writer → 桌面渲染与编辑复查
```

支持的规则元素重建为原生结构，复杂纹理与科学插图保留原始视觉内容。[表示方式与研究方向](docs/roadmap.md)。

## 当前版本与 Roadmap

当前为 **Research Preview**，拟作为首个 `v0.1.0-preview.1` 版本发布，已验收的核心基线保持不变。主要面向结构明确的流程图、技术路线图和简单科研示意图。复杂 AI 生图的完整重建、通用不规则对象分离，以及 Linux/WSL 运行仍需要进一步研发或验证。

后续方向包括更完整的对象分组、透明图层、选择性矢量化和更细粒度的科学对象重建。FAST / BALANCED / HIGH 是研发方向，并非三个已完成的产品模式。[Roadmap](docs/roadmap.md)。

详细范围见[展示说明](docs/showcase.md)和[最小例验证](docs/verification.md)。[AI 图像实验说明](docs/experimental-ai.md)记录主演示所用的诊断修复及其范围。

## License / Acknowledgements

**MIT License · Copyright (c) 2026 Fang Zhe。**

[LICENSE](LICENSE) · [第三方声明](NOTICE.md) · [来源清单](PROVENANCE.md) · [展示素材来源](docs/showcase-provenance.md)

SF/I2P 历史方法参考见第三方声明。第三方内容保留其各自许可证。AI 原图及其衍生展示文件采用 **CC BY 4.0**，见[素材署名与许可](examples/showcase-ai/ASSET_PERMISSION.md)。

维护者：[Fang Zhe](https://github.com/fun4fang)。
