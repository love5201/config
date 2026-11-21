# md2anki — Markdown 同步 Anki 工具

将 Bear 等 Markdown 笔记中的卡片增量同步至 Anki（通过 AnkiConnect），支持**裸代码块**与**单行卡片**两种格式，支持多级卡组（`#toanki/...`）、挂起状态控制（`@suspend` / `suspend`）、跨行注释过滤（`%%...%%`）、本地/Base64 图片自动上传、差异预览（`--diff`）、安全删卡与空卡组清理。

---

## 一、 前置准备

1. **Python 环境与依赖**
* Python 3.8+
* 安装 `requests`：
```bash
pip install requests

```




2. **Anki 与 AnkiConnect 配置**
* 安装并启用 **AnkiConnect** 插件（默认监听 `http://localhost:8765`）。


* 在 Anki 中提前创建以下两个笔记类型（Model），确保字段名与脚本一致：


* **`Basic-md2anki`**（问答题）：包含字段 `front`、`back`、`uuid`、`source`


* **`Cloze-md2anki`**（挖空题，需勾选 Cloze 类型）：包含字段 `text`、`extra`、`uuid`、`source`







---

## 二、 卡片书写规范

脚本会自动剥离 `%% ... %%` 跨行注释（仅用于笔记内私密批注，不进入 Anki）。所有挖空 `{{内容}}` 默认统一转换为 `{{c1::内容}}`。

### 1. 裸代码块格式（适合多行问答、长解析、带紧跟图片）

卡组行写在 ````` 之前，格式为 `#toanki/一级卡组/二级卡组`，行尾可加 `@suspend` 表示默认挂起（暂停）该卡片：

```
#toanki/计算机网络/TCP
\```
uuid: tcp-congestion-001
Tags: 面试, 拥塞控制
TCP拥塞控制的核心阶段有哪些？
---
1. 慢启动（Slow Start）
2. 拥塞避免（Congestion Avoidance）
3. 快重传与快恢复（Fast Retransmit & Recovery）
%% 这是块内注释，同步时会自动移除 %%
\```
```
* **块头部**：支持 `uuid:`（必填）和 `Tags:`（可选，逗号/中文逗号/空格分隔）。
* **正反面分隔**：使用**独占一行的 `---**` 分隔正面与背面。若正面包含 `{{...}}`，自动识别为 `Cloze-md2anki`；否则为 `Basic-md2anki`（需有背面内容）。
* **紧跟图片**：裸代码块闭合 ````` 后，若紧跟单张或多张图片行（中间不隔空行，支持 Bear 的 `<!-- {"width":...} -->` 尾缀），会自动提取并追加到卡片背面。

### 2. 单行卡片格式（适合短句、列表项、脚注行）

无需以 `- ` 开头，只要**同一行**同时包含 `#toanki/卡组名`（卡组名不可为空）和 `<!-- uuid:... -->` 注释即可识别。行首的 `[^1]:`、`- `、`1. ` 等列表/脚注前缀会自动过滤。

* **Basic 单行**（用 `::` 分隔正反面）：

```
线性回归的损失函数通常使用::均方误差 (MSE) #toanki/机器学习2 <!-- uuid: eee1731945600124;suspend -->
```
* **Cloze 单行**（含 `{{...}}` 挖空，下方缩进的 `- ` 子行可作为背面补充）：
```
1. {{TCP}} 是面向连接的可靠传输协议 #toanki/计算机网络/基础 <!-- uuid: net-tcp-002; tags: 网络,基础 -->
  - 常见同类协议为 UDP
```
> **注释属性灵活性**：`<!-- ... -->` 内支持任意空格、全/半角分号（`;` / `；`）与冒号（`:` / `：`）、键名大小写不敏感（如 `uuid`、`tags`、`suspend`）。
> 
> 

---

## 三、 命令行参数与使用示例

```bash
python3 md2anki.py [笔记目录] [选项]

```

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `dir` | Markdown 笔记所在目录

 | `./notes`

 |
| `--toanki` | 正式写入/更新 Anki；不加仅预览（不依赖 AnkiConnect）

 | `False`

 |
| `--diff` | 差异预览模式：对比本地与 Anki，输出新增/更新/待删列表，不修改 Anki

 | `False`

 |
| `--allow-delete` | 删除 Anki 中存在但本地 Markdown 已移除的 `uuid` 笔记，并清理关联空卡组

 | `False`

 |
| `--yes`, `-y` | 删除时跳过交互输入 `DELETE` 确认

 | `False`

 |
| `--max-delete` | 单次允许删除的最大笔记数防误删阈值（`0` 表示不限制）

 | `20`

 |
| `--quiet` | 安静模式，减少过程日志输出

 | `False`

 |
| `--append-tags` | 为所有解析出的卡片追加默认标签（逗号分隔）

 | `bear-app`

 |
| `--tsv` | 导出卡片汇总 TSV 文件路径

 | `anki_export.tsv`

 |
| `--media-out-dir` | 可选：将处理的图片额外保存到指定本地目录

 | `""`（不额外落盘）

 |

### 常用命令

```bash
# 1. 本地解析预览（无需启动 Anki）
python3 md2anki.py ./notes

# 2. 查看与 Anki 的新增/更新/待删差异
python3 md2anki.py ./notes --diff

# 3. 正式同步到 Anki（不删卡）
python3 md2anki.py ./notes --toanki

# 4. 正式同步并允许清理已移除笔记及空卡组（带备份与防误删阈值）
python3 md2anki.py ./notes --toanki --allow-delete --max-delete 20

```

---

## 四、 注意事项与安全机制

* **UUID 唯一性**：同一批次扫描出重复 `uuid` 时会直接报错终止，不会执行推送、删除或导出。


* **模型不可直接变更**：同一 `uuid` 若在 Anki 中已存在但笔记类型（`Basic-md2anki` 与 `Cloze-md2anki`）发生变化，脚本会跳过该条同步并提示，需在 Anki 中手动删除旧笔记后再重新同步。


* **删卡备份与保护**：启用 `--allow-delete` 时，待删笔记数超过 `--max-delete` 会中止；确认删除前会自动生成 `anki_delete_backup_YYYYMMDD_HHMMSS.json` 备份文件。
