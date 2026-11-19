# md2anki 脚本 README

## 功能简介

把 Markdown 文件里自定义的 `<anki>` 卡片同步到 Anki，基于 Anki-Connect。

- ✅ 支持 basic / cloze 两种卡片类型
- ✅ 可选 `suspend` 布尔标记：带则暂停卡片，不带则取消暂停
- ✅ 自动转换路径：`#anki/地理/中国地理` → Anki卡组 `地理::中国地理`
- ✅ 全局 UUID（id）查重，重复直接终止，防止冲突
- ✅ 更新已有笔记、新建笔记、自动移动卡片到目标卡组
- ✅ `--allow-delete` 模式：删掉md里不存在的笔记；删除后自动清理空的**子卡组**，父卡组只告警不删除
- ✅ 导出 `anki_export.tsv` 备份（包含 suspend 状态）
- ✅ 调试日志：新增/更新打印 note_id、card_ids；quiet 参数关闭 debug 输出

## 前置依赖

1. Anki 安装插件：Anki-Connect，地址：[https://foosoft.net/projects/anki-connect/](https://foosoft.net/projects/anki-connect/)
2. 开启 Anki，保证 Anki-Connect 监听 `[http://localhost:8765](http://localhost:8765)`
3. Python 依赖

```
pip install pyyaml requests
```

4. 在Anki中提前建好两个笔记类型（模型名称严格一致）

- `Basic-md2anki`：字段 `front`、`back`、`uuid`
- `Cloze-md2anki`：字段 `text`、`extra`、`uuid`> 
> ⚠️ uuid 字段必须存在，脚本依靠此字段定位笔记

## 语法示例

### 1. basic（问答卡片，不暂停）

```
<anki id="b001" type="basic" tags="计算机网络,TCP">
deck: #anki/计算机网络
front: TCP和UDP区别？
back: 1.TCP面向连接；UDP无连接
2.TCP可靠；UDP不可靠
</anki>
```

### 2. cloze（挖空卡片，suspend暂停）

```
<anki id="abc002" type="cloze" tags="地理,首都" suspend>
deck: #anki/地理/中国地理
text: 中国的首都是{{c1::北京}}
extra: 拓展：北京是直辖市
</anki>
```

- 属性说明
  - `id`：全局唯一标识（uuid），**不可重复**
  - `type`：`basic` / `cloze`
  - `tags`：逗号分隔标签，可选
  - `suspend`：布尔标记，只写关键字、不用赋值；有则暂停，无则取消暂停
  - `deck`：`#anki/` 开头，`/` 自动转为Anki卡组分隔符 `::`
- 标签必须成对闭合：`<anki> ... </anki>`

## 命令使用

```
# 预览模式：只解析打印，不写入Anki
python md2anki.py ./notes

# 写入Anki（不自动删除，只新增/更新）
python md2anki.py ./notes --toanki

# 写入Anki + 开启自动删除（md中消失的笔记会被Anki删除，同步清理空子卡组）
python md2anki.py ./notes --toanki --allow-delete

# 安静模式，关闭debug日志
python md2anki.py ./notes --toanki --quiet
```

## 执行流程

1. 扫描目标目录全部 `.md` 文件，提取 `<anki>` 块
2. 校验所有 id，存在重复 → 直接退出，不做任何改动
3. 预览模式：输出解析结果，结束
4. toanki 模式：
   - 根据 uuid 查询Anki笔记
   - 找到：更新字段、标签、移动卡组，同步 suspend 状态
   - 没找到：新建笔记，同步 suspend 状态
5. 开启 `--allow-delete`：
   - 找出Anki内uuid在本次md中不存在的笔记，批量删除
   - 遍历被删除笔记所属卡组，空子卡组自动删除
   - 父卡组为空只打印警告，不会删除
6. 导出 `anki_export.tsv`

## 日志标识

- `🔄` 处理卡片
- `➕` 新增笔记
- `♻️` 更新笔记
- `📂` 自动创建卡组
- `📦` 卡片移动卡组
- `⏸️` 暂停卡片
- `▶️` 取消暂停卡片
- `🗑️` 删除笔记
- `🧹` 删除空的子卡组
- `⚠️` 警告，不会中断流程
- `❌` 错误，单条卡片失败不影响其他卡片继续执行

## 注意事项 & 避坑

1. Anki必须保持打开状态，Anki-Connect正常运行，否则报错
2. 模型名称、字段名称不能写错（Basic-md2anki / Cloze-md2anki）
3. id一旦分配尽量不要修改；修改id脚本会当成一条全新笔记新增
4. suspend 是卡片状态，每次同步都会按md标记强制覆盖Anki里原有暂停状态
5. `--allow-delete` 是高危参数：md删掉anki块，anki笔记就会被删除，重要笔记谨慎使用
6. cloze 的 text 里必须包含 `{{c1::内容}}` 挖空语法，否则会跳过该卡片
7. 导出文件 `anki_export.tsv` 在脚本同目录，每次运行覆盖

## 文件结构示例

```
md2anki.py
notes/
├─ input.md
└─ 地理.md
anki_export.tsv
```

---

如果你想，我可以再单独写一个【快速检查脚本】，只扫描md，检查有没有重复id、语法错误，不碰Anki。
