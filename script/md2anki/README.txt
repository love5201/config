================================================================================
Markdown 同步 Anki 与 YouTube 片段内嵌完整指南 (v3.10)
================================================================================

一、项目概述与工作流架构
--------------------------------------------------------------------------------
本方案打通了「Bear / Markdown 笔记 -> 本地 Python 同步脚本 -> Anki 桌面端 /
AnkiMobile 移动端」的完整制卡与复习链路，核心特性包括：

1. 多形态制卡语法：同时支持「单行卡片」「多行代码块卡片」及「自定义 noteType
   模板卡片」，支持 Cloze 挖空与 Basic 问答。
2. 媒体全自动托管：自动提取本地图片、内联 Base64 图片及音视频文件，通过
   AnkiConnect 上传至 Anki 媒体库并转换为标准标签。
3. 增强型卡片渲染：借助 xmarkdown.js 渲染 Markdown，同时在模板层完整保护
   MathJax 数学公式、单波浪线 ~下划线~、原生音频胶囊栏及固定安全区页脚。
4. 跨平台 YouTube 片段内嵌：通过 `[ytv:...]` 极简短码 + GitHub Pages 静态
   中转页（yt.html），解决 AnkiMobile（iOS WKWebView）播放「错误 153」，
   并自动提供携带起止时间的 YouTube 原生外链兜底。


二、核心文件清单
--------------------------------------------------------------------------------
1. md2anki.py
   - 本地同步主脚本，扫描指定目录下的 .md 文件，解析卡片并通过 AnkiConnect
     （http://localhost:8765）增量新增、更新或安全删除 Anki 笔记。
2. yt.html
   - 静态 HTTPS 中转页面（部署地址：https://love5201.github.io/yt.html），
     为移动端 WebView 提供合法的 Referer 请求头以正常加载 YouTube 播放器。
3. cloze背面模板（HTML + JS）
   - 卡片背面模板，负责提取顶部音频按钮、保护公式与 `[ytv:...]` 短码、调用
     xmarkdown.js 渲染正文，并触发 MathJax 排版。
4. css（卡片样式表）
   - 统一控制挖空高亮、720px 居中阅读栏、extra 钢青蓝提示框、底部固定
     source 页脚（适配 iPhone 底部安全区）、顶部胶囊音频栏及暗黑模式。


三、Markdown 制卡语法规范（md2anki.py）
--------------------------------------------------------------------------------
所有卡片均通过 `#toanki/卡组路径` 指定目标卡组（层级以 `/` 分隔，自动映射为
Anki 的 `::` 层级），并可通过 `@suspended` 或 `@active` 控制卡片挂起状态。

【写法一：单行卡片】
在行内同时包含 `#toanki/卡组` 与 HTML 注释元数据 `<!-- uuid: xxx; ... -->`：

1. Single Basic（问答卡，使用 `::` 分隔正反面）：
   问题内容 :: 答案内容 #toanki/英语/词汇 <!-- uuid: 1001; tags: vocab,unit1 -->

2. Single Cloze（挖空卡，使用 `{{...}}` 标记挖空）：
   爱因斯坦提出了{{质能方程}} $E=mc^2$。 #toanki/物理 <!-- uuid: 1002 -->

   Single Cloze 附加 extra（补充解析）的三种方式（可组合使用）：
   - 注释内指定：`<!-- uuid: 1002; extra: 补充说明 -->`
   - 挖空后接 `::`：`问题{{挖空}} :: 补充说明 #toanki/物理 <!-- uuid: 1002 -->`
   - 紧跟子列表：在单行卡片下方缩进或顶格写 `- 补充条目`

3. 单行卡片紧跟媒体：
   - 行尾直接追加 `![alt](image.png)` 或 `[audio](clip.mp3)`
   - 或在下一行（非列表卡片）紧跟独立的图片/音视频行，自动归入背面。

【写法二：多行代码块卡片】
第一行为卡组声明，紧跟独占一行的 ``` 代码围栏：

#toanki/计算机/网络 @active
```
Tags: http, tcp
uuid: 2001
什么是三次握手？
---
客户端与服务器建立 TCP 连接时交换的三个报文段：
1. SYN
2. SYN-ACK
3. ACK
```
![示意图](assets/tcp_handshake.png)

说明：
- 若正文含 `{{...}}` 自动识别为 Cloze 模板（Cloze-md2anki），`---` 后为 extra；
- 若正文不含 `{{...}}` 自动识别为 Basic 模板（Basic-md2anki），`---` 为必填；
- 闭合 ``` 后紧跟的图片/音视频行（空行前）会自动提取并追加到卡片背面。

【写法三：自定义模板卡片（noteType）】
支持围栏或无围栏写法，首行写 `#toanki/卡组`，次行写 `noteType: 模板名`：

#toanki/新概念/第一册
noteType: NCE1_Core
uuid: 3001
tags: lesson1
Lesson: Lesson 1
Sentence_EN: Excuse me!
Sentence_CN: 对不起！
Target_Word: excuse
![配图](assets/l1.jpg)
[朗读](assets/l1.mp3)

说明：紧跟的音频自动合并至 `Audio` 字段，图片自动合并至 `Image` 字段。


四、YouTube 视频片段短码规范（[ytv:...]）
--------------------------------------------------------------------------------
在卡片正文（text）或补充字段（extra）中写入短码，模板会自动将其展开为
16:9 响应式播放器 + 中转页链接 + 原生 YouTube 跳转按钮。

1. 标准语法（第 1 段固定为视频 ID，后续参数以 `|` 分隔，顺序任意）：
   [ytv:VIDEO_ID|start|end|autoplay|lazy=0]

2. 参数说明：
   - VIDEO_ID（必填）：YouTube 视频编号（如 4g5HOnKB4vY）。
   - start（可选）   ：起始时间，支持纯秒数（1265）、分秒（21:05）或
                       时分秒（01:02:10）；留空默认从 0s 开始。
   - end（可选）     ：结束时间，格式同 start；留空默认播放至结尾。
   - autoplay（可选）：默认【不自动播放】。
                       开启自动播放：写 `autoplay` / `autoplay=1` / `autoplay=true`
                       关闭自动播放：写 `noautoplay` / `autoplay=0`
   - lazy（可选）    ：默认【开启懒加载】（loading="lazy"）。
                       关闭懒加载（立即加载）：写 `nolazy` / `eager` / `lazy=0`
                       开启懒加载：写 `lazy` / `lazy=1`

3. 常用示例：
   - 完整片段（分秒，默认懒加载、不自动播放）：
     [ytv:4g5HOnKB4vY|21:05|23:33]
   - 完整片段（秒数）：
     [ytv:4g5HOnKB4vY|1265|1413]
   - 仅指定开始时间 + 开启自动播放：
     [ytv:4g5HOnKB4vY|21:05|autoplay]
   - 仅指定结束时间 + 关闭懒加载：
     [ytv:4g5HOnKB4vY||23:33|lazy=0]
   - 播放整段视频：
     [ytv:4g5HOnKB4vY]


五、md2anki.py 命令行使用与安全机制
--------------------------------------------------------------------------------
运行前请确保桌面端 Anki 已启动，且已安装并启用 AnkiConnect 插件（2055492159）。

1. 常用命令：
   - 仅本地解析预览（不连接/不写入 Anki）：
     python3 md2anki.py ./notes

   - 差异预览（对比本地 Markdown 与 Anki 现有笔记的新增/更新/待删）：
     python3 md2anki.py ./notes --diff

   - 正式同步（新增与更新笔记，不删除 Anki 中多余笔记）：
     python3 md2anki.py ./notes --toanki

   - 正式同步并清理 Markdown 中已删除的笔记及空卡组：
     python3 md2anki.py ./notes --toanki --allow-delete

2. 常用 CLI 参数：
   --append-tags   默认追加标签（默认: bear-app，逗号分隔）
   --data-dir      数据与日志输出目录（默认: data）
   --max-delete    单次允许删除的最大笔记数保护阈值（默认: 20，0 为不限制）
   --yes, -y       删除笔记时跳过终端输入 DELETE 的交互确认
   --quiet         安静模式，减少终端逐条输出

3. 自动生成的审计与备份文件（默认位于 data/ 目录）：
   - data/anki_export.tsv          : 全量标准化导出的 TSV 备份表
   - data/suspend_audit.csv        : 挂起状态核对表（当 Anki 中卡片已手动挂起
                                     而 Markdown 未写 @active 时，脚本会尊重
                                     Anki 原有挂起状态并记录到此表）
   - data/meta_dash_warnings.csv   : 非标准注释符号告警表（检测全角 `！` 或 `—`）
   - data/delete_backup/*.json     : 执行删除前自动导出的待删笔记完整 JSON 快照


六、模板部署与跨平台播放配置
--------------------------------------------------------------------------------
1. 静态中转页部署（yt.html）：
   - 将 yt.html 推送至 GitHub 仓库并开启 GitHub Pages，确保可通过 HTTPS
     网页形式访问（当前配置为 https://love5201.github.io/yt.html）。
   - 切勿使用 cdn.jsdelivr.net 作为中转页地址（其对 .html 返回 text/plain）。
   - yt.html 支持查询参数：v（必填）、start、end、autoplay=1、nocookie=0
     （默认使用 youtube-nocookie.com，遇极个别限制嵌入的视频可传 nocookie=0）。

2. 解除 macOS 桌面端自动播放拦截：
   - AnkiMobile（iOS WKWebView）支持有声自动播放，但 macOS 桌面端 Anki 使用
     Chromium 内核（QtWebEngine），默认拦截跨源 iframe 的有声自动播放。
   - 在 macOS 终端执行以下命令写入开机自启配置，即可在桌面端放行有声自动播放：

     cat << 'EOF' > ~/Library/LaunchAgents/com.user.anki.autoplay.plist
     <?xml version="1.0" encoding="UTF-8"?>
     <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
     <plist version="1.0">
     <dict>
       <key>Label</key>
       <string>com.user.anki.autoplay</string>
       <key>ProgramArguments</key>
       <array>
         <string>/bin/launchctl</string>
         <string>setenv</string>
         <string>QTWEBENGINE_CHROMIUM_FLAGS</string>
         <string>--autoplay-policy=no-user-gesture-required</string>
       </array>
       <key>RunAtLoad</key>
       <true/>
     </dict>
     </plist>
     EOF
     launchctl load ~/Library/LaunchAgents/com.user.anki.autoplay.plist

   - 执行后按 Cmd + Q 完全退出并重启 Anki 即可生效。

3. 模板细节说明：
   - 背面模板的 PROXY_BASE_URL 需与实际部署的 yt.html 地址保持一致。
   - 若背面模板中的 parseYtvShortcode 尚未更新为支持 autoplay / lazy 旗标的
     版本，请确保使用包含 `autoplay` 与 `lazy` 参数解析的最新版模板脚本。
================================================================================
