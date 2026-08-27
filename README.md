# AI Pinyin Input

一个大模型拼音输入法项目。仓库同时提供 IBus 前端和 fcitx5 前端；两者复用同一套 Python LLM 候选、SQLite 缓存、用户记忆和领域词库。

当前实现偏 MVP：输入法前端负责按键捕获、候选窗和提交文本；Python 后端负责缓存、词库上下文和 OpenAI-compatible LLM 候选生成。详细设计见 [design.md](design.md)。

[更新日志](changelog.md)

## 特性

- 支持 OpenAI-compatible Chat Completions API。
- 支持本地 llama.cpp、Ollama、DeepSeek、OpenRouter 等兼容服务。
- 支持 OpenAI-compatible SSE 流式返回，以 `|` 分隔并增量展示 18 个候选。
- 会把已上屏历史文本和当前输入框光标前后文放入 system content，辅助模型判断语境。
- 拼音输入期间不把原始拼音写入当前输入框，只在 IBus 弹出区域显示输入内容和候选。
- 用户选择候选后才提交中文到输入框。
- 焦点切换时自动清空原始拼音、候选和辅助文本。
- 支持快捷键切换中英文输入模式，默认 `Ctrl+Space`。
- IBus 状态栏/面板会显示当前输入模式：`中` 或 `英`。
- 支持 SQLite 候选缓存，常用候选会被提升排序。
- 支持导入 `.dict.json` 自定义领域词库，领域词会作为 LLM 上下文参与长拼音纠错。
- 拼音输入过程中输入 ASCII 标点或符号时，不会退出输入，会把符号连同拼音一起发送给 LLM。
- 未开始输入拼音时数字键直通应用；已经开始输入拼音后数字会进入缓冲区，不会中断输入。
- 中文模式下开启 CapsLock 时，英文按键会直接交给当前应用。
- 模型失败或超时时不会阻塞输入，可按回车提交原始拼音。
- 支持本地常用词兜底候选。
- Ctrl、Alt、Super、Meta 快捷键以及功能键、方向键等会直通应用，避免被输入法屏蔽。

## 依赖

```bash
sudo apt update
sudo apt install -y ibus python3 python3-gi gir1.2-ibus-1.0 python3-requests sqlite3
```

如果当前系统没有启用 IBus：

```bash
im-config -n ibus
```

然后注销并重新登录。

## 安装

```bash
chmod +x scripts/install-user.sh
./scripts/install-user.sh
ibus restart
```

之后打开 `ibus-setup`，添加：

```text
Chinese -> AI 拼音输入法
```

也可以在 GNOME 设置中通过 `Settings -> Keyboard -> Input Sources` 添加。

如果命令行切换可用，也可以执行：

```bash
ibus engine ai-pinyin
```

## 安装 fcitx5 LLM 输入法

fcitx5 前端会调用同一份 Python 后端，因此会保留：

- `~/.config/ibus-ai-pinyin/config.json`
- `~/.config/ibus-ai-pinyin/cache.sqlite3`
- 已导入领域词库
- 用户选择缓存和自动学习记忆

安装：

```bash
chmod +x scripts/install-fcitx5-user.sh
./scripts/install-fcitx5-user.sh
fcitx5 -r
fcitx5-remote -s ai-pinyin
```

Ubuntu 24.04 的 fcitx5 5.1 会从系统 addon 目录发现 shared-library 输入法，所以安装脚本会用 `pkexec` 把以下文件复制到系统目录：

```text
/usr/lib/x86_64-linux-gnu/fcitx5/ai-pinyin.so
/usr/share/fcitx5/addon/ai-pinyin.conf
/usr/share/fcitx5/inputmethod/ai-pinyin.conf
```

运行时 Python 后端仍安装在用户目录：

```text
~/.local/share/ibus-ai-pinyin/fcitx5_backend.py
~/.local/share/ibus-ai-pinyin/ibus_ai_pinyin/
```

确认当前输入法：

```bash
fcitx5-remote -n
```

返回 `ai-pinyin` 即表示 fcitx5 LLM 输入法已启用。

当前 fcitx5 前端已支持主路径：拼音缓冲、空格请求 LLM 候选、数字/空格/回车提交、退格和 ESC 清空。IBus 版已有的候选修正、翻页更多候选和流式增量会继续保留在 IBus 前端，fcitx5 前端后续再补齐。

## 迁移词库到 fcitx5 自带拼音

如果只想把 ai-pinyin 的用户词和领域词导出给 fcitx5 自带 `pinyin` 输入法，可以运行：

```bash
chmod +x scripts/migrate-to-fcitx5.sh scripts/export-fcitx5-pinyin-dict.py
./scripts/migrate-to-fcitx5.sh
fcitx5 -r
```

这条路径不会启用 LLM 候选，只会生成 fcitx5/libime 额外词库。

## 配置

### 图形配置界面

安装后可以从 IBus 面板中的“设置”打开 GTK4 配置界面，也可以直接运行：

```bash
~/.local/share/ibus-ai-pinyin/settings.py
```

界面支持：

- 创建、删除和切换多个 LLM 配置
- 每个模型单独配置 Base URL、模型名称和 API Key；下拉列表直接显示模型名称
- 通用配置超时、采样参数、最大输出 Token、流式输出、系统代理和请求正文日志
- Endpoint 固定为 `/chat/completions`，模型配置不能覆盖
- 编辑包含 `{history}` 的 system 提示词与包含 `{pinyin}` 的 user 模板
- 手动刷新查看 `~/.cache/ibus-ai-pinyin/engine.log`，日志视图不会自动滚动
- 按相同模型汇总最近 30 天调用次数、成功率、Token 消耗和平均响应耗时

Token 和耗时统计保存在配置目录的 SQLite 数据库中。API Key 只写入本机配置文件，不写入运行日志或统计表。

首次启动会生成：

```text
~/.config/ibus-ai-pinyin/config.json
```

修改配置后需要重启 IBus 才会生效：

```bash
ibus restart
ibus engine ai-pinyin
```

默认配置使用本地 OpenAI-compatible 服务：

```json
{
  "api": {
    "base_url": "http://127.0.0.1:8080/v1",
    "api_key": "",
    "api_key_env": "OPENAI_API_KEY",
    "model": "qwen3-0.6b",
    "endpoint": "/chat/completions",
    "timeout_ms": 5000,
    "stream_timeout_ms": 5000,
    "temperature": 0.1,
    "top_p": 0.8,
    "max_tokens": 64,
    "stream": true,
    "proxy_enabled": false,
    "thinking": {
      "enabled": false,
      "type": "disabled"
    },
    "cache_optimization": {
      "enabled": "auto",
      "provider": "",
      "log_usage": true
    },
    "extra_body": {}
  },
  "input": {
    "max_buffer_length": 120,
    "candidate_page_size": 9,
    "recent_context_items": 6,
    "recent_context_chars": 80,
    "recent_context_idle_timeout_seconds": 1800,
    "surrounding_context_enabled": true,
    "surrounding_context_before_chars": 80,
    "surrounding_context_after_chars": 40,
    "default_mode": "zh",
    "toggle_key": {
      "enabled": true,
      "key": "space",
      "modifiers": ["Control"]
    }
  },
  "candidate": {
    "max_candidates": 18,
    "fallback_to_raw_pinyin": true
  },
  "prompt": {
    "system": "拼音转中文。输出18个按概率排序的候选，以|分隔，无解释。可保留英文、数字和符号。\n\n{history}",
    "user_template": "现在输入:{pinyin}"
  },
  "dictionary": {
    "enabled": true,
    "path": "~/.config/ibus-ai-pinyin/cache.sqlite3",
    "max_candidates": 5
  }
}
```

图形界面保存后可选择“保存并重启 IBus”。从仓库修改代码后，需要重新安装再重启：

```bash
./scripts/install-user.sh
ibus restart
ibus engine ai-pinyin
```

IBus 实际运行的是 `~/.local/share/ibus-ai-pinyin` 下的安装副本，只执行 `ibus restart` 不会自动同步仓库代码。

### DeepSeek 示例

```json
{
  "api": {
    "base_url": "https://api.deepseek.com",
    "api_key": "sk-...",
    "model": "deepseek-v4-flash",
    "endpoint": "/chat/completions",
    "timeout_ms": 5000,
    "stream_timeout_ms": 5000,
    "max_tokens": 256,
    "stream": true,
    "proxy_enabled": false,
    "thinking": {
      "enabled": false,
      "type": "disabled"
    },
    "cache_optimization": {
      "enabled": "auto",
      "provider": "",
      "log_usage": true
    },
    "extra_body": {}
  }
}
```

`thinking.enabled=false` 会在请求体中加入：

```json
{
  "thinking": {
    "type": "disabled"
  }
}
```

这适用于支持关闭思考的 OpenAI-compatible 服务。其他厂商需要额外请求字段时，可以放到 `api.extra_body`。

`deepseek-v4-flash` / `deepseek-v4-pro` 使用 DeepSeek 服务端自动上下文缓存。`api.cache_optimization.enabled=auto` 时，客户端会在检测到 DeepSeek base URL 或模型名后使用缓存友好的 user content 布局。历史输入和输入框上下文位于 system content，当前拼音位于最后一条 user message。

默认协议不使用 Tool Call 或 JSON 数组。模型直接输出以 `|` 分隔的候选，客户端解析 SSE 增量并立即显示完整候选。流式请求 5 秒超时后会立即重试一次；第二次仍超时就停止远程请求并使用已有或本地候选。

日志会记录 DeepSeek 返回的 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`，用于确认缓存是否命中。如果通过转发服务调用 DeepSeek 且 URL/模型名里不含 `deepseek`，可以显式设置：

```json
{
  "api": {
    "cache_optimization": {
      "enabled": true,
      "provider": "deepseek",
      "log_usage": true
    }
  }
}
```

如果服务不支持 `thinking` 请求字段，把 `thinking.enabled` 设为 `null`，客户端就不会发送该字段。例如：

```json
{
  "api": {
    "base_url": "https://integrate.api.nvidia.com/v1",
    "api_key": "nvapi-...",
    "model": "stepfun-ai/step-3.5-flash",
    "endpoint": "/chat/completions",
    "temperature": 0,
    "thinking": {
      "enabled": null,
      "type": "disabled"
    }
  }
}
```

提示词可以在图形配置界面修改。system 模板必须保留 `{history}`，user 模板必须保留 `{pinyin}`。

### 中英文切换快捷键

默认快捷键是 `Ctrl+Space`：

```json
{
  "input": {
    "default_mode": "zh",
    "toggle_key": {
      "enabled": true,
      "key": "space",
      "modifiers": ["Control"]
    }
  }
}
```

`default_mode` 可设置为：

```text
zh
en
```

英文模式下，输入法不会拦截普通按键，所有输入都会直接交给当前应用。再次按切换快捷键会回到中文模式。

IBus 状态栏/面板会显示当前模式：

```text
中
英
```

状态显示通过 IBus component 的 `icon_prop_key=InputMode` 和引擎内同名 property 实现。

例如改成 `Alt+Space`：

```json
{
  "input": {
    "toggle_key": {
      "enabled": true,
      "key": "space",
      "modifiers": ["Alt"]
    }
  }
}
```

### Ollama 示例

```json
{
  "api": {
    "base_url": "http://127.0.0.1:11434/v1",
    "api_key": "ollama",
    "model": "qwen3:0.6b",
    "endpoint": "/chat/completions",
    "timeout_ms": 1200
  }
}
```

### llama.cpp server 示例

```bash
llama-server \
  -m ./models/qwen3-0.6b-q4_k_m.gguf \
  --host 127.0.0.1 \
  --port 8080 \
  -c 512 \
  -n 64
```

## 使用

```text
输入 nihao
按空格请求候选
候选不准确时继续输入补充说明，再按空格调整候选
按 - 或 = 排除当前候选并生成下一组候选
按 1-9 选择候选
按 Ctrl+1 到 Ctrl+9 修改对应候选
选中候选后按 Delete 删除该词；缓存、用户动态词库和领域词库中的同名词都会清理
按空格提交当前第一个候选
按回车提交当前候选或原始拼音
按 Esc 清空
按 Backspace 删除
```

拼音输入过程中，原始拼音不会写入当前输入框。IBus 候选弹窗会显示当前拼音和候选结果，只有选择候选或回车提交时才会写入输入框。

候选列表显示后，如果继续输入字母、数字或符号，输入法不会立刻提交当前候选，也不会把这段内容当成新的拼音重新查询；它会把后续输入作为本轮候选的补充说明显示在辅助文本里。再次按空格时，输入法会把原始拼音、当前候选和补充说明一起发送给 LLM，用来修正候选中不准确的地方。补充说明为空时，按空格仍然提交当前选中的候选。

候选列表显示时，按 `-` 或 `=` 会模拟翻页：输入法把当前候选作为排除列表发给 LLM，请求生成一组新的候选。如果模型没有返回新候选，会继续保留当前候选列表。

如果已经开始输入拼音，继续输入 ASCII 标点或符号不会退出输入法缓冲区。例如输入 `nihao,` 后按空格，发送给 LLM 的内容就是 `nihao,`。如果当前没有拼音缓冲区，符号仍然直接交给当前应用。

### 历史输入上下文

用户每次选择候选并上屏后，输入法会在内存中记录最终选择的中文结果。未选中的候选不会写入历史上下文，历史拼音也不会发送给模型。

下一次调用 LLM 时，已上屏候选会用逗号连接，并替换 system 模板中的 `{history}`。例如模板为 `之前用户输入的内容是:"{history}"`，用户依次输入“输入”“了”“什么”“东西”时，实际内容为：

```text
之前用户输入的内容是:"输入,了,什么,东西"
```

`{history}` 只代表纯历史文本，不自带“之前输入的内容”等前缀，说明文字和标点由 system 模板控制。历史轮次只保存在当前 engine 进程内，重启输入法后会清空。默认最多保留最近 `input.recent_context_items=6` 轮，并按 `input.recent_context_chars=80` 限制字符数。如果超过 `input.recent_context_idle_timeout_seconds=1800` 秒没有输入，下一次输入时会先清空历史轮次。

### 当前输入框上下文

请求候选时，输入法会通过 IBus surrounding text API 获取当前光标前后的文本，并放入 system content：

```text
当前输入框上下文：
光标前文本：已经输入的前文
光标后文本：光标后面的文字
```

默认最多发送光标前 80 个字符和光标后 40 个字符，可通过 `input.surrounding_context_before_chars`、`input.surrounding_context_after_chars` 调整，或将 `input.surrounding_context_enabled` 设为 `false` 关闭。应用不支持 surrounding text、密码框或敏感输入框不提供上下文时，输入法会自动降级为空上下文。

### 模型输出协议

默认要求模型直接返回 18 个候选，以 `|` 分隔，例如 `你好|您好|你号`。流式解析器会忽略 SSE 的注释和 `event:`、`id:`、`retry:` 控制行，并在收到每个完整候选后立即更新候选窗。兼容代码仍能解析 JSON 或 Tool Call 响应，但默认请求不发送工具定义。

数字键按输入状态区分处理：没有拼音缓冲区时直接输入数字；已经开始输入拼音后，数字会进入缓冲区；候选列表显示时，`1-9` 继续用于选择候选。

中文模式下开启 CapsLock 后，英文按键会直接交给当前应用，便于临时输入大写英文。

## 候选词修改和用户动态词库

候选列表显示后，可以按 `Ctrl+数字` 进入候选修改模式。例如 `Ctrl+1` 修改第一个候选。

修改模式下：

```text
左右方向键 / Home / End 移动光标
Backspace / Delete 删除文字
输入中文或符号会直接插入
输入拼音后按空格生成局部替换候选
按 1-9 选择局部替换候选
按 Enter 提交修正结果并保存记忆
按 Ctrl+Enter 只提交，不保存记忆
按 Esc 返回原候选列表
```

修正提交后，输入法会记录修正日志；如果修正结果包含 2 到 12 个汉字，会自动加入用户动态词库。下次输入相同完整拼音时，用户动态词会优先出现在候选里；长拼音命中用户词时，也会作为少量上下文发送给 LLM。

默认配置：

```json
{
  "memory_dictionary": {
    "enabled": true,
    "path": "~/.config/ibus-ai-pinyin/cache.sqlite3",
    "auto_learn": true,
    "send_to_llm": true,
    "max_context_terms": 8,
    "exact_match_candidate": true,
    "auto_learn_min_han": 2,
    "auto_learn_max_han": 12,
    "default_weight": 80,
    "max_weight": 120,
    "record_corrections": true
  }
}
```

管理用户动态词库：

```bash
scripts/user-memory.py list
scripts/user-memory.py search 鸿灵
scripts/user-memory.py disable 鸿灵
scripts/user-memory.py enable 鸿灵
scripts/user-memory.py delete 鸿灵
scripts/user-memory.py export > user-memory.dict.json
```

## 自定义领域词库

输入法支持导入标准 `.dict.json` 词库。导入后，词库只作为 LLM 上下文使用，不会直接加入候选列表：

- 普通短输入不会被词库前缀候选污染，例如 `zhi` 不会因为词库里有 `知识库` 而直接展示 `知识库`。
- 长拼音短语输入时，命中的领域词会作为上下文传给 LLM，并对 LLM 候选重排。例如输入 `honglingzhishikujiansuogongneng` 时，词库可提供 `鸿灵`、`知识库`、`搜索`，帮助模型输出 `鸿灵知识库搜索功能`。

### 词库格式

最小示例：

```json
{
  "version": "1.0",
  "name": "我的项目词库",
  "entries": [
    {
      "term": "鸿灵",
      "pinyin": ["hong ling"],
      "short": ["hl"],
      "type": "product",
      "weight": 100,
      "enabled": true
    },
    {
      "term": "搜索",
      "pinyin": ["sou suo", "jian suo"],
      "short": ["ss"],
      "type": "tech",
      "weight": 85,
      "enabled": true
    }
  ]
}
```

`type` 只能使用以下值：

```text
product project system module feature organization person tech abbreviation business mixed other
```

不要使用 `concept`、`tool`、`algorithm`、`framework` 等非标准类型。完整格式见 [docs/dictionary-format-v1.md](docs/dictionary-format-v1.md)。

### 使用 skill 生成词库

项目内提供了 `ibus-ai-pinyin-dict-skill/`，可用于从项目文档、Wiki、术语表中生成 `.dict.json`。

典型提示：

```text
请使用 ibus-ai-pinyin-domain-dictionary skill，
从下面资料中提取适合输入法使用的专有名词，
生成 dev/mydict.json 词库文件。
```

生成词库时注意：

- 精准优先，不要把普通词大量加入词库。
- 核心领域词必须提供完整 `pinyin`，不能只给 `short`。
- 如果用户常输入同义词，但期望输出规范词，把同义输入的拼音也加到规范词的 `pinyin` 中。例如期望 `jiansuo` 输出 `搜索`，则写 `"pinyin": ["sou suo", "jian suo"]`。
- 生成后先校验，再导入。

### 校验和导入

校验 skill 生成的词库：

```bash
python3 ibus-ai-pinyin-dict-skill/scripts/validate_dict.py dev/mydict.json
```

只检查导入效果，不写数据库：

```bash
python3 scripts/import-dictionary.py dev/mydict.json --dry-run
```

导入到默认数据库：

```bash
python3 scripts/import-dictionary.py dev/mydict.json
```

默认数据库路径：

```text
~/.config/ibus-ai-pinyin/cache.sqlite3
```

导入后重启 IBus：

```bash
ibus restart
ibus engine ai-pinyin
```

### 缓存和 LLM 的关系

候选来源会融合：

```text
用户动态词库完整匹配候选
LLM 新结果
SQLite 历史缓存
本地兜底候选
```

词库本身不直接输出候选。当长输入命中领域词上下文时，缓存不会直接截断请求，输入法仍会调用 LLM 补充结果；缓存只作为后备候选参与融合，避免旧缓存污染覆盖新的词库纠错结果。

LLM 首轮请求 18 个候选，IBus 候选窗每页显示 9 个。融合用户动态词、SQLite 历史缓存、本地兜底和 LLM 结果后，超过一页可以翻页；翻到已有候选不足的页面时才继续请求更多候选。

SQLite 历史缓存只记录用户实际选择并上屏的候选，不会把 LLM 每次输出的整组候选全部写入缓存。来自 SQLite 历史缓存的候选会在候选窗中显示 `*` 标识；命中用户动态词库或领域词库的候选会显示 `#` 标识。如果同一个候选同时来自缓存和知识库，会同时显示两个标识。候选窗显示时，按 `Delete` 会跨 SQLite 缓存、用户动态词库和领域词库删除所选词。

## 日志和缓存

日志：

```text
~/.cache/ibus-ai-pinyin/engine.log
```

缓存：

```text
~/.config/ibus-ai-pinyin/cache.sqlite3
```

开启“记录请求正文”后，日志会记录 LLM request body（不含 Authorization）；常规日志保留模型请求耗时、HTTP 状态、超时重试和错误。候选窗刷新、缓存命中等高频流水日志不会记录。日志使用滚动文件，`engine.log` 单文件最大 10 MB，并保留一个 `engine.log.1` 历史文件。设置界面只在打开或点击“刷新”时读取最近 500 行，不会自动刷新或滚动。

请求正文可能包含当前输入框文字和历史输入，调试完成后建议关闭该开关。需要手工清理时可以运行：

```bash
truncate -s 0 ~/.cache/ibus-ai-pinyin/engine.log
```

## 开发验证

```bash
python3 -m py_compile engine.py ibus_ai_pinyin/*.py tests/*.py
python3 tests/test_core.py
```

## License

MIT. See [LICENSE](LICENSE).
