# README 更新（新增简易行语法，方案B；原有 `<anki>` 块说明保留）

## md2anki 脚本说明

本脚本可以把 Markdown 笔记同步到 Anki，**支持两套卡片语法，可混写在同一个 md 文件**：

1. 原有 `<anki>...</anki>` YAML 块格式（稳定不变）
2. 新增简易短横线列表格式（适合快速记知识点）

> 
> 前置要求：Anki 安装 AnkiConnect 插件，模型名称固定为 `Basic-md2anki`、`Cloze-md2anki`
> 运行命令
> 
> 
> ```
> # 预览（推荐先跑这个检查解析是否正确，不会改动Anki）
> python script.py ./notes
> 
> # 同步写入Anki
> python script.py ./notes --toanki
> 
> # 同步 + 删除md里不存在的旧笔记 + 自动清理空的子卡组
> python script.py ./notes --toanki --allow-delete
> 
> # 安静模式，减少日志输出
> python script.py ./notes --quiet
> ```

---

## 一、旧格式：`<anki>` 块（不变）

```
<anki id="qqq123" type="cloze" suspend tags="心理学,记忆">
deck: #ankid/心理学
text: 艾宾浩斯遗忘曲线提出于{{c1::1885年}}
extra: 遗忘先快后慢
</anki>
```

- `id`：uuid，唯一标识
- `type`：`basic` / `cloze`
- `suspend` 可选，带上则卡片暂停
- `tags` 可选逗号分隔，自动追加 `bear-app`
- `deck`：`#ankid/xxx/yyy` 自动转为 Anki 卡组 `xxx::yyy`

---

## 二、新格式：简易列表行（方案B，推荐快速录入）

✅ 三个条件**必须同时满足**，否则不解析：

1. 以 `- `（半角横杠+空格）开头
2. 本行包含 `#ankid/`
3. 本行包含 `<!-- anki-id: ... -->` 属性注释

### 属性注释规则（全部收拢在同一个HTML注释内，`;` 分隔）

```
<!-- anki-id: 唯一id; suspend; tags: tag1,tag2 -->
```

- `anki-id: xxx`：必填，卡片uuid
- `suspend`：无值flag，写上即代表 suspend=True，不写默认False
- `tags: a,b,c`：预留扩展，当前简易行默认标签固定为 `bear-app`

### 两种卡片自动识别逻辑

1. **`::` → Basic（正反面卡片）**

```
- 线性回归的损失函数通常使用::均方误差 (MSE) #ankid/机器学习 <!-- anki-id: eee1731945600124; suspend -->
  - MSE 对异常值敏感
  - 导数简单，便于梯度下降
```

- `::` 左侧 = front；右侧 = back
- 该行下方缩进的 `- ` 子条目，全部合并到 extra 字段

2. **无 `::` → Cloze（自动斜体转挖空）**

```
- 艾宾浩斯遗忘曲线的提出时间是*1885年*。 #ankid/心理学 <!-- anki-id: qqq1731945600123 -->
  - 遗忘规律：先快后慢
  - 研究材料：无意义音节
```

- 行内 `*内容*` 自动替换成 Anki 挖空 `{{c1::内容}}`
- 下方缩进子项归入 extra

### 示例汇总

```
# 普通cloze，不暂停
- 艾宾浩斯遗忘曲线的提出时间是*1885年*。 #ankid/心理学 <!-- anki-id: qqq1731945600123 -->
  - 遗忘规律：先快后慢

# basic卡片，同步标记suspend（暂停）
- 线性回归的损失函数通常使用::均方误差 (MSE) #ankid/机器学习 <!-- anki-id: eee1731945600124; suspend -->
  - MSE对异常值敏感
```

---

## 三、通用规则

1. uuid 全局唯一，出现重复直接终止运行，防止覆盖错乱
2. 标签：
   - `<anki>`块：自定义tags + 强制追加 `bear-app`
   - 简易行：固定默认 `bear-app`
3. 卡组路径：`#ankid/a/b/c` → Anki卡组 `a::b::c`，不存在会自动创建
4. `--allow-delete` 模式：只删除当前md里不存在uuid的笔记；**只会自动删除已经变空的子卡组，父卡组为空仅打印警告、不删除**
5. 每次运行会输出 `anki_export.tsv` 备份卡片清单

## 四、报错提示

- 简易行满足3个匹配条件，但正文解析失败（cloze找不到斜体挖空、::分割异常等）→ 打印警告，跳过本条，其余卡片继续解析
- 缺少 `anki-id` → 警告跳过

如果你想要，我可以再单独给一份【精简版README，适合直接放在notes文件夹里当说明】。
