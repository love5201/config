================================================================================
                    md2anki (v3.17) 完整使用与维护手册
================================================================================

md2anki 是一套面向 Bear / Markdown 笔记的 Anki 自动化制卡与跨平台渲染方案，
专为 macOS Anki (QtWebEngine) 与 iOS AnkiMobile (WKWebView) 设计，主打：
  - 基于 UUID 的精准增量同步（新增、更新、移动卡组、防误删备份）
  - 离线优先的 Markdown 渲染、公式排版（MathJax）与统一视觉样式
  - 基于 edge-tts 的本地预生成语音与多音字纠偏，离线零延迟播放
  - YouTube 视频片段短码嵌入与 Bear 自定义协议链接保护


--------------------------------------------------------------------------------
一、核心组件清单
--------------------------------------------------------------------------------

1. md2anki-v3_17.py
   Python 命令行同步脚本。负责扫描 Markdown 目录、解析卡片、处理本地/Base64
   图片与音视频、调用 edge-tts 预合成语音、通过 AnkiConnect 推送至 Anki，并
   导出核对报表与删除备份。

2. _md2anki.js（存放于 Anki collection.media 目录）
   卡片通用渲染预处理/后处理脚本（Basic 与 Cloze 模板共用）。负责：
   - 提取原生音频播放按钮到卡片顶部胶囊栏（.anki-audio-bar）
   - 解析 [ytv:...] YouTube 嵌入短码（经静态中转页规避 AnkiMobile 错误 153）
   - 保护 <a> 超链接（防止 bear:// 协议被过滤）、<iframe>、MathJax 公式及单 <br>
   - 支持 ~下划线~ 语法（与 ~~删除线~~ 隔离）
   - 触发 MathJax 重新排版，并在跨卡复用 WebView 时自动重置状态

3. _xmarkdown.js（存放于 Anki collection.media 目录）
   Markdown 渲染引擎本地离线副本。

4. style.css（粘贴至 Anki 笔记模板的“样式”栏）
   Basic-md2anki 与 Cloze-md2anki 通用样式表：
   - 挖空高亮配色（正面橙色、背面绿色）
   - extra / back 统一浅钢青蓝圆角提示框，单换行紧凑、空行分段清晰
   - 顶部音频胶囊播放栏（支持多音频自动编号与触控反馈）
   - 底部固定毛玻璃来源页脚（已适配 iPhone 底部安全区）
   - 完整暗黑模式（.nightMode）适配


--------------------------------------------------------------------------------
二、环境准备与部署步骤
--------------------------------------------------------------------------------

1. 安装 Python 依赖（建议 Python 3.9+）
   pip install requests edge-tts

   * 注：如暂不使用 [tts:...] 功能，可仅安装 requests；离线同步可加 --no-tts。

2. 安装并启动 AnkiConnect 插件
   - 打开桌面端 Anki -> 工具 -> 插件 -> 获取插件 -> 输入代码：2055492159
   - 重启 Anki，保持桌面端 Anki 处于运行状态（默认监听 http://localhost:8765）

3. 将渲染脚本放入 Anki 本地媒体库（macOS）
   媒体库路径通常为：
   ~/Library/Application Support/Anki2/<你的配置文件名>/collection.media/
   （默认配置文件名一般为 "User 1" 或 "账户 1"）

   操作步骤：
   (1) 将 _md2anki.js 复制到 collection.media/ 目录
   (2) 下载 _xmarkdown.js 到同一目录（以下划线 _ 开头可避免被“检查媒体”误删）：
       curl -L "https://cdn.jsdelivr.net/gh/love5201/config/script/md2anki/xmarkdown.js" \
         -o ~/Library/Application\ Support/Anki2/"User 1"/collection.media/_xmarkdown.js
   (3) 如卡片中需要点击 bear:// 等自定义协议超链接，请确保 _md2anki.js 的
       protectContent() 中包含 <a> 标签保护逻辑。
   (4) 在 macOS 端 Anki 按 Y 执行一次同步，脚本将自动同步到 iOS AnkiMobile。

4. 在 Anki 中配置笔记类型与卡片模板

   [笔记类型 A] Basic-md2anki
   所需字段：front, back, uuid, source
   - 正面模板：
     <div data-xmd hidden>{{front}}</div>
     {{#source}}<div class="source-note">{{source}}</div>{{/source}}
     <script src="_md2anki.js"></script>
     <script src="_xmarkdown.js" defer></script>

   - 背面模板：
     {{FrontSide}}
     <hr id="answer" />
     <div class="back-box" data-xmd hidden>{{back}}</div>
     {{#source}}<div class="source-note">{{source}}</div>{{/source}}

   [笔记类型 B] Cloze-md2anki
   所需字段：text, extra, uuid, source
   - 正面模板：
     <div data-xmd hidden>{{cloze:text}}</div>
     {{#source}}<div class="source-note">{{source}}</div>{{/source}}
     <script src="_md2anki.js"></script>
     <script src="_xmarkdown.js" defer></script>

   - 背面模板：
     <hr id="answer" />
     <div data-xmd hidden>{{cloze:text}}</div>
     {{#extra}}<div class="extra-box" data-xmd hidden>{{extra}}</div>{{/extra}}
     {{#source}}<div class="source-note">{{source}}</div>{{/source}}
     <script src="_md2anki.js"></script>
     <script src="_xmarkdown.js" defer></script>

   - 样式表：将 style.css 完整粘贴到上述两个笔记类型的“样式”栏中。


--------------------------------------------------------------------------------
三、Markdown 制卡语法规范
--------------------------------------------------------------------------------

所有卡片均通过 #toanki/卡组/子卡组 指定目标卡组（自动转换为 Anki 的 :: 层级），
并必须包含全局唯一的 uuid。笔记中的 %% ... %% 会被自动视为注释剥离。

【写法 1】裸代码块（推荐用于多行 Basic / Cloze 卡片）
--------------------------------------------------
#toanki/生活/面料
```
Tags: 服装, 常识
uuid: fabric_2026-11-23T09:27:00
六种常见服装面料的核心特点是什么？
---
【15字速记口诀】
氨弹涤耐磨，莱滑莫吸垂，醋凉蚕光泽。

- ~氨~纶 = 高~弹~
- ~涤[dí]~纶 = ~耐磨~
```
[面料口诀.wav](面料口诀.wav)

说明：
- 代码块首部支持 Tags:（可选，逗号或空格分隔）和 uuid:（必填）。
- 正面若包含 {{内容}}，自动识别为 Cloze-md2anki（转为 {{c1::内容}}），--- 后
  方内容写入 extra 字段（无 --- 时 extra 为空）。
- 正面不含 {{}} 时识别为 Basic-md2anki，必须包含独占一行的 --- 分隔正背面。
- 紧跟在闭合 ``` 下方（无空行）的图片、音频、视频链接会自动提取并附到背面。
- 换行规则：单个回车保持紧凑换行；中间空一行会渲染为独立段落。

【写法 2】自定义 noteType 块（支持有围栏 ``` 或无围栏）
--------------------------------------------------
#toanki/英语/新概念1
noteType: NCE1_Core
uuid: nce1_001
Tags: Lesson1
Lesson: Lesson 1
Sentence_EN: [tts:Excuse me. Is this your handbag?|sentence|show]
Sentence_CN: 对不起。这是您的手提包吗？
Target_Word: handbag
Target_Word_Hint: n. 手提包
Phonetic_Notes: /ˈhændbæɡ/
![示意图](handbag.png)

说明：
- 支持任意自定义 Anki 模板字段（Key: Value，支持多行值）。
- 紧跟在区块后的音频自动合并至 Audio 字段，图片自动合并至 Image 字段。

【写法 3】单行卡片（适合快速记录）
--------------------------------------------------
- 单行 Basic（用 :: 分隔正背面，不支持 extra）：
  - 什么是幂等性？ :: 多次执行结果与一次执行一致 #toanki/技术/架构 <!-- uuid: idem_01; tags: 后端 -->

- 单行 Cloze（支持注释 extra、行内 :: extra 以及紧跟的 - 子列表）：
  - HTTP 状态码 {{429}} 表示请求过多 :: 限流常见返回码 #toanki/技术/网络 <!-- uuid: http_429; extra: Too Many Requests -->
    - 客户端应配合 Retry-After 退避重试

* 注意：单行元数据注释请使用标准半角 <!-- ... -->。脚本可容错识别全角 ！ 或
  破折号 —，但会在终端告警并记录到 data/meta_dash_warnings.csv。

【挂起状态控制】
--------------------------------------------------
- 卡组行末尾加 @suspended（或单行注释加 suspended）：强制挂起该卡片。
- 卡组行末尾加 @active（或单行注释加 active）：强制取消挂起。
- 不写上述标记：尊重 Anki 现有状态（不会覆盖你在 Anki 手动挂起的卡片），并
  将“Anki 已挂起但笔记未标 @suspended”的差异导出至 data/suspend_audit.csv。

【行内排版增强】
--------------------------------------------------
- 单波浪线 ~文字~   -> 渲染为下划线 <u>文字</u>
- 双波浪线 ~~文字~~ -> 保留标准删除线
- 公式支持 $...$、$$...$$、\(...\)、\[...\]，由 MathJax 渲染
- Bear 内部链接自动转为可点击 <a> 标签


--------------------------------------------------------------------------------
四、[tts:...] 文字转语音与多音字处理
--------------------------------------------------------------------------------

脚本在执行 --toanki 同步时，自动调用 edge-tts 将 [tts:...] 短码预生成为 MP3，
缓存到 data/tts_cache/ 并上传至 Anki 媒体库，输出标准 [sound:bear_tts_xxx.mp3]。

1. 基本语法（选项写在末尾，顺序任意）
   [tts:待朗读文本|选项1|选项2...]

   常用示例：
   - 纯语音（默认不显示文字，音频进入顶部胶囊栏）：
     [tts:氨弹涤耐磨，莱滑莫吸垂]
   - 保留正文并附带语音（加 |show）：
     [tts:Excuse me. Is this your handbag?|show]
   - 指定场景别名、音色、语速与音调：
     [tts:核心原理拆解|干货解说|show]
     [tts:handbag|word|slow|show]
     [tts:慢速朗读测试|xiaoyi|rate=-20%|pitch=+5Hz|show]

2. 默认音色与自动语种检测
   - 含中文字符时默认使用：zh-CN-XiaoxiaoNeural（晓晓女声）
   - 纯英文/其他文本默认使用：en-US-EmmaMultilingualNeural（Emma 美音女声）

3. 内置音色简写对照表（也支持 voice=完整音色名 或 voice=简写别名）
   简写                                       对应 Microsoft Neural 音色
   ----------------------------------------------------------------------------------
   zh / cn / zh-f / xiaoxiao / 晓晓 / 日常对话  zh-CN-XiaoxiaoNeural（中文女声，默认）
   xiaoyi                                     zh-CN-XiaoyiNeural（中文女声，年轻清脆）
   zh-m / cn-m / yunxi / 云希 / 短视频          zh-CN-YunxiNeural（中文男声）
   干货解说 / 云扬                              zh-CN-YunyangNeural（中文男声，沉稳解说）
   学术商务 / 云间                              zh-CN-YunjianNeural（中文男声，专业播报）
   zh-tw                                      zh-TW-HsiaoChenNeural（台湾女声）
   zh-hk                                      zh-HK-HiuGaaiNeural（粤语女声）
   us / en / en-us / emma / sentence          en-US-EmmaMultilingualNeural（美音例句，默认）
   ava / dialog                               en-US-AvaMultilingualNeural（美音对话，舒缓）
   aria                                       en-US-AriaNeural（美音女声，清晰播报）
   jenny / word                               en-US-JennyNeural（美音单词，干脆清晰）
   us-m                                       en-US-GuyNeural（美音男声）
   uk / en-gb / sonia                         en-GB-SoniaNeural（英音女声，标准平稳）
   libby                                      en-GB-LibbyNeural（英音女声，年轻轻快）
   uk-m                                       en-GB-RyanNeural（英音男声）
   jp / ja                                    ja-JP-NanamiNeural（日语女声）

4. 语速、音调与显示开关
   - 显示控制：show / text / keep / show=1（保留原文）；hide / show=0（仅语音，默认）
   - 语速控制：slow (-15%)、slower (-25%)、fast (+15%)、faster (+25%)、rate=±N%
   - 音调控制：pitch=±NHz

5. 多音字与注音处理（三重机制，按优先级生效）
   (1) 行内替读 {原词=替读}（最高优先级）：
       例：[tts:氨{弹=谈}{涤=敌}耐磨|日常对话|show]
       卡片显示为“氨弹涤耐磨”，实际喂给 TTS 朗读“氨谈敌耐磨”。
       与 {{挖空}} 完全兼容，互不冲突。
   (2) 自动剥离方括号注音/音标：
       合成前自动过滤如 ~涤[dí]~纶、handbag [ˈhændbæɡ] 中的非中文方括号内容
       及 Markdown 修饰符/挖空标记，避免朗读拼音或符号；卡片正文原样保留。
   (3) 全局替读词典 data/tts_replace.json：
       在 data/ 目录下新建 tts_replace.json（以下划线 _ 开头的键自动忽略）：
       {
         "_comment": "TTS 多音字与专业词替读表，长词优先匹配",
         "涤纶": "敌轮",
         "重读": "虫读",
         "行长": "航掌"
       }
       修改词典后重新同步，音频哈希会自动更新并重新合成覆盖。


--------------------------------------------------------------------------------
五、[ytv:...] YouTube 视频嵌入短码
--------------------------------------------------------------------------------

由 _md2anki.js 在卡片渲染时解析，支持内嵌播放及“在 YouTube 中打开”兜底链接。

语法格式：
  [ytv:视频ID|开始时间|结束时间|开关参数...]

说明：
- 时间支持纯秒数（如 125）、mm:ss（如 02:05）或 hh:mm:ss（如 1:02:05）。
- 仅指定结束时间可留空开始时间：[ytv:dQw4w9WgXcQ||01:30]
- 可选开关（顺序任意）：
  - autoplay / autoplay=1（默认关闭）
  - noautoplay / autoplay=0
  - lazy / lazy=1（默认开启懒加载）
  - nolazy / eager / lazy=0
- 注：YouTube 已停用外部强制画质接口，清晰度由播放器自动选择或点击齿轮手动切换；
  如需固定低画质及完全离线播放，建议直接放入本地 MP4 视频文件。


--------------------------------------------------------------------------------
六、命令行用法与参数详解
--------------------------------------------------------------------------------

常用命令示例：

1. 仅本地解析预览（不连接 Anki，不合成 TTS）：
   python3 md2anki-v3_17.py ./notes

2. 差异预览（连接 Anki 对比新增/更新/待删，不改动数据）：
   python3 md2anki-v3_17.py ./notes --diff

3. 正式同步写入 Anki（含图片/音视频上传与 TTS 合成）：
   python3 md2anki-v3_17.py ./notes --toanki

4. 正式同步 + 清理 Markdown 中已删除的卡片与空卡组：
   python3 md2anki-v3_17.py ./notes --toanki --allow-delete

5. 安静模式 + 离线跳过 TTS：
   python3 md2anki-v3_17.py ./notes --toanki --quiet --no-tts

完整参数列表：
  dir                       待扫描的 Markdown 笔记目录（默认：./notes）
  --toanki                  正式写入 Anki；不加则仅本地预览
  --diff                    差异预览模式，显示新增/更新/待删清单，不改动 Anki
  --allow-delete            删除 Anki 中已不存在于 Markdown 的 uuid 笔记，并清理空卡组
  --yes, -y                 执行删除时跳过交互输入 DELETE 确认
  --max-delete N            防误删上限：单次待删笔记超过 N 条时中止（默认 20，0 不限制）
  --quiet                   安静模式，减少终端日志输出
  --append-tags TAGS        每张卡片自动追加的标签，逗号分隔（默认：bear-app）
  --data-dir DIR            辅助数据与缓存存放目录（默认：data）
  --tsv PATH                导出 TSV 路径或文件名（默认：data/anki_export.tsv）
  --suspend-csv PATH        挂起状态核对 CSV（默认：data/suspend_audit.csv）
  --meta-warn-csv PATH      非标准注释符号告警 CSV（默认：data/meta_dash_warnings.csv）
  --media-out-dir DIR       可选：将解码/合成的媒体文件额外保存一份到本地目录
  --tts-cache-dir DIR       TTS 本地缓存目录（默认：data/tts_cache）
  --tts-replace PATH        多音字替读词典 JSON 路径（默认：data/tts_replace.json）
  --tts-zh-voice VOICE      覆盖默认中文音色（默认：zh-CN-XiaoxiaoNeural）
  --tts-en-voice VOICE      覆盖默认英文音色（默认：en-US-EmmaMultilingualNeural）
  --no-tts                  临时跳过 [tts:...] 语音合成


--------------------------------------------------------------------------------
七、data/ 目录产出说明
--------------------------------------------------------------------------------

运行后脚本会在 data/ 目录（可通过 --data-dir 修改）维护以下文件：
  data/
  ├── anki_export.tsv              # 全量卡片 TSV 导出快照
  ├── suspend_audit.csv            # Anki 已挂起但笔记未标 @suspended 的核对表
  ├── meta_dash_warnings.csv       # 单行卡片非标准注释符号（全角！/—）告警表
  ├── tts_replace.json             # （用户按需新建）全局 TTS 多音字替读词典
  ├── tts_cache/                   # edge-tts 合成的 MP3 持久化缓存，避免重复联网
  └── delete_backup/               # 删除前自动生成的 JSON 备份，支持人工找回


--------------------------------------------------------------------------------
八、常见问题排查
--------------------------------------------------------------------------------

1. 提示“无法连接到 AnkiConnect (http://localhost:8765)”
   - 确认桌面端 Anki 已打开、AnkiConnect 插件（2055492159）已启用且端口未被代理拦截。

2. 提示“检测到重复 uuid，终止运行”
   - 同步前脚本会强制校验全目录 UUID 唯一性，按终端打印的重复 ID 修改笔记即可。

3. 提示“模型变更，跳过该卡片同步”
   - AnkiConnect 不支持直接修改已有笔记的 Note Type（例如 Basic 改为 Cloze）；
     需先在 Anki 中手动删除旧笔记，再重新执行 --toanki 同步。

4. 卡片中 bear:// 超链接预览无法点击
   - 确认 collection.media/_md2anki.js 的 protectContent() 已加入 <a> 标签保护，
     并已在 Anki 同步刷新；注意在正式复习或浏览器预览窗口中测试外部协议唤起。

5. 修改了 _md2anki.js 或 style.css 后手机端未生效
   - 在 macOS Anki 按 Y 完成同步，再打开 AnkiMobile 下拉或点击同步拉取最新媒体与模板。
