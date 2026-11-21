# 剪辑时间线交换工具（Clip Timeline Exchange）

把一种声明支持范围的剪辑决策表**真实导入**内部模型、**导出**到另一种格式，并按
内部模型**重建对应预览序列**。所有时间用精确有理数表示，逐帧比较交换前后实际选中
的源帧；目标格式无法表达的内容进入**带位置的损失报告**，绝不静默删除。

## 怎样启动界面

零第三方 Python 依赖（仅标准库 + 系统 ImageMagick 用于渲染 PNG 联系表）：

```bash
cd timeline_exchange
python3 -I web/server.py --port 8765
# 浏览器打开
#   http://127.0.0.1:8765/
```

界面操作：

1. 点击 **“载入混合帧率示例工程”**（或在文本框粘贴自己的 TL-JSON / CMX EDL）。
2. 选择源格式与目标格式，点 **“执行交换并重新读取”**。
3. 页面并排显示**交换前 / 交换后（重新读取）**的编辑结构、导出文档原文、损失报告；
   “目标能力清单”可选择窄化清单（如 `cmx3600-narrow-wipe`）。
4. **能力清单 × 契约样例**面板逐项列出能力、是否有样例、实测结果、像素/结构两条结论
   与首个丢失位置；“运行场景”用随机顺序复跑六个场景。
5. **任意输出帧 → 源片与区间**面板对当前游标帧返回源区间、源帧号与需要的重连方式，
   并可直接运行“按物料身份重连”和“校验源引用”。
6. **拖动游标**，下方预览由剪辑交换内核返回该记录时刻实际选中的源帧（真实 PNG），
   并显示源帧号、源时间码、有理秒与转场进度；可切换“交换前 / 交换后”对照。
7. 内核失败（如文档解析错误）会在界面顶部红色回传，而不是吞掉。

命令行等价入口：

```bash
# 富格式 -> CMX（有不可表达内容时退出码 2，损失写入报告）
python3 -I scripts/run_exchange.py \
  --in test_project/project.tl.json --from tl-json --to cmx3600 \
  --rate 24000/1001 --media-dir test_project \
  --out /tmp/out.edl --report /tmp/loss.json

# CMX -> 富格式（借助素材注册表把 reel 名解析回真实素材，无损）
python3 -I scripts/run_exchange.py \
  --in test_project/project.edl --from cmx3600 --to tl-json \
  --rate 24000/1001 --source-registry test_project/project.tl.json \
  --media-dir test_project --out /tmp/out.tl.json --report /tmp/loss2.json
```

一键重建素材、工程、测试与渲染：`scripts/run_all.sh`。

## 能力清单 × 可执行契约样例

`tlx/capabilities.py` 把目标格式的每项能力写成数据（`TargetProfile`），
`tlx/contracts.py` 的每个样例都真实跑一遍**导入 → 导出 → 重新读取 → 逐帧比对**，
结果为 `reversible` / `flattened` / `rejected` 之一：

| 能力 | TL-JSON | CMX 3600 | 备注 |
| --- | --- | --- | --- |
| `transition:cut/dissolve/wipe` | 可逆 | 可逆 | 源帧与剪点均保留 |
| `transition:fade` | 可逆 | **已展平** | CMX 只有 `D`，重读成 dissolve |
| `transition:push/slide` | 可逆 | **已展平** | 导出为硬切，源帧不变 |
| `track:multi_video` | 可逆 | **拒绝** | 第二视频轨无法表达 |
| `gap:explicit` | 可逆 | 可逆 | 损失报告告警，但由记录时间跳变重建 |
| `source:mixed_rate` | 可逆 | 可逆 | 每源各自时钟 |
| `clip:reverse` | 可逆 | **拒绝** | 无法表达倒放，画面改变 |
| `media:baked` | 可逆 | **已展平** | reel 名可寻址，但丢烘焙身份 |
| 覆盖率 | 11/11 有样例 | 11/11 有样例 | `uncovered_capabilities` 必须为空 |

`reversible` = 像素与剪辑结构都保真；`flattened` = 像素一致但剪点不再可编辑；
`rejected` = 画面/结构至少一项改变且已显式报告。**三种结果都是实测**，不是文档声明。

## 窄化目标能力

`capabilities.narrow(profile, "transition:wipe")` 生成严格子集。
`contracts.compare_profiles` 对两个清单各跑全部样例，只列出结果发生变化的样例，
并给出其首个丢失编辑语义的记录时间码：

```bash
python3 -I scripts/run_contracts.py   # 写 test_project/contracts/narrowing.json
```

把 CMX 的 wipe 拿掉后，只有 `wipe` 样例从 `reversible` 变成 `flattened`，
首个丢失位置 `00:00:00:20`；其余 10 个样例不变。

## 媒体重连器

`tlx/relink.py` 按剪辑本身决定要什么：

- `clip.baked == True`（已展平的转场）→ 请求**烘焙媒体**，只生成一个 `baked` 区间；
- 保留剪点的转场 → 请求**两个源区间**（`outgoing` + `incoming`）；
- 普通切 → 单个源区间。

候选素材必须同时匹配 `media_ref`、`content_id` 与 `timecode_origin`。
同片长、同像素但不同 `media_ref` 的烘焙替身会被拒绝；同名但源时码不同的候选也会被拒绝。

## 同一镜头源上的三种硬情况

`scenarios.three_capability_timeline` 在同一批素材上同时布置：

- 倒放区间（`reverse=True`，源帧沿源时钟递减）；
- 与相邻视频轨在记录时间上**交叠**的转场（交叠处 V2 赢得画面，转场窗口内仍可溯源到两段）；
- 跨速率剪点（CAM_C 29.97 对 24 记录的源帧号）。

交换窗口的任意输出帧都用 `tlx/trace.py` 查回源片与区间，返回
`reconnect_one_range` / `reconnect_two_ranges` / `baked_media`。

## 像素一致 ≠ 剪辑保真

`tlx/fidelity.py` 始终给两条独立结论：

- `pixel.match`：逐帧解码到的**媒体内容指纹 + 帧号**是否一致（显示名不参与）；
- `structure.faithful`：片段、源区间、转场、倒放、空隙与烘焙状态是否一致。

同一份画面、一个项目仍可编辑、另一个已烘焙时，报告是
`pixel_match=True, edit_structure_faithful=False`，而不是含混的“有损/无损”。

## 源引用校验器

`tlx/validate.py` 沿 `media_ref` 定位，不用显示名。它显式区分：

- `unresolved_ref`：ref 不在目录；即使存在同名且能正常播放的片段也**不替代**；
- `content_mismatch` / `timecode_mismatch`：ref 命中但指纹或源时码不符；
- 每个问题都带片段 id 与记录时间码，并标 `needs_human`，供人工修剪点。

`scenarios.shared_name_swap` 用两份共用 `CAM_A` 显示名、源时码不同的工程，
以随机顺序重建，验证只有按 ref 才稳定；按名字查会得到 2 个候选（歧义），
交叉放入对方素材库时校验器拒绝并指出需人工处理的剪点。

## 两种交换格式

| 格式 | 标识 | 表达能力 |
| --- | --- | --- |
| **TL-JSON v1** | `tl-json` | 多视频/音频轨、显式空隙、全部转场、混合帧率、非零起始时间码；时间同时给出有理秒与时间码，无损 |
| **CMX 3600 EDL** | `cmx3600` | 单视频轨、`C` 切、`D` 溶解、`W` 划像；记录时间码；无显式空隙构造、无第二视频轨、不保留混合帧率的重定时元数据 |

内部模型支持的转场：`cut / dissolve / fade / wipe / push / slide`。
CMX 只能表达 `cut / dissolve / fade / wipe`；`push`、`slide` 导出为硬切并生成
`transition_not_expressible` 损失条目（源帧本身不变）。空隙是模型中的显式对象，
导出 EDL 时生成 `gap_not_expressible`，重新导入时依据记录时间跳变**重建空隙**。

## 精确时间，不做浮点累积

- 所有时间字段是 `fractions.Fraction` 秒（如 NTSC 一帧 = `1001/24000`）。
- TL-JSON 拒绝裸浮点秒，只接受有理字符串或时间码，杜绝长片累积漂移。
- 帧选择采用半开区间 `[i/r, (i+1)/r)` 的精确整数地板；混合帧率素材按**各自帧率**
  解析源时间码、在各自时钟上推进，源时间 = `source_in + (record_time - clip_start)`。
- `tests/test_exchange.py::test_no_float_drift_over_feature_length` 对两小时 NTSC
  时间线逐帧求和，断言结果与精确有理值完全相等、时间码不偏帧。

## 真实导入 / 导出 / 重新读取验证

交换流水线（`tlx/exchange.py`）：

1. 导入源文档到内部 `Timeline`；
2. 探针检查每个素材引用是否在磁盘存在（`preview.probe_sources`）；
3. 导出目标文档，同时收集损失报告；
4. **重新读取导出文档**得到第二条时间线；
5. 对每个记录帧调用同一个 `preview.select_frame` 内核，比较两侧的
   `kind / source_id / source_frame_index / transition`。

一键写入契约产物（能力实测、窄化差异、六个场景证据）：

```bash
python3 -I scripts/run_contracts.py --seed 7
python3 -I scripts/run_relink.py --seed 7 --out test_project/contracts/relink.json
```

产物在 `test_project/contracts/`：

- `capability_outcomes.json`：每个清单 × 每个样例的实测结果与首个丢失位置；
- `narrowing.json`：窄化只影响相关样例的证据；
- `scenarios.json`：六个场景的原始证据与 `pass` 标记；
- `relink.json`：重连接受/拒绝记录与源引用校验结果。

测试工程 `test_project/`（由脚本生成，非手工编造）覆盖：

- 混合素材帧率：CAM_A `24000/1001`、CAM_B `25/1`、CAM_C `30000/1001`，记录帧率 `24000/1001`；
- 非零起始时间码 `01:00:00:00`；
- 相邻裁切（C0001/C0002 首尾相接，断言边界帧选择切入片段）；
- 6 帧显式空隙、6 帧溶解、CMX 不可表达的 push、第二视频轨覆盖层。

渲染产物在 `test_project/renders/`：

- `before_strip.png` / `after_strip.png`：同一记录帧网格上的真实帧联系表（ImageMagick
  从帧包 PNG 合成，不是占位图）；
- `frame_audit.json`：逐记录帧的源帧身份审计；
- `missing_slate.png`：故意破坏 CAM_B 引用后的诊断板，写明源 id、缺失路径、所需源帧号
  与记录时间码——素材缺失时**定位引用**，不播放任意占位图。

素材目录 `test_project/sources/catalog.json` 显式登记 `media_ref`：`CAM_A` 与
`CAM_A_BAKED` **像素完全相同**但属于不同媒体；`CAM_B_ALT` 可与 `CAM_B` 同名但源时码
不同。这两对正是重连与校验必须区分的用例。

## 目录结构

```
tlx/
  timecode.py   精确有理时间、帧率、时间码
  model.py      内部时间线/轨/片段/空隙/源/转场模型
  tl_json.py    富格式 TL-JSON 导入导出
  cmx.py        CMX 3600 EDL 导入导出 + 损失收集
  loss.py       带位置的损失报告
  preview.py    媒体探针与精确帧选择内核
  render.py     SVG 实时预览块 + PNG 联系表
  exchange.py   导入→导出→重新读取→逐帧比对编排
  capabilities.py 目标能力清单、窄化与能力×样例清单
  contracts.py  可执行契约样例与清单对比
  relink.py     媒体重连（烘焙媒体 / 单区间 / 双区间，按身份核验）
  trace.py      任意输出帧 → 源片与区间溯源
  fidelity.py   像素一致与剪辑结构保真的分离报告
  validate.py   沿 media_ref 的源引用校验
  media.py      媒体目录与内容指纹
web/            标准库 http.server 后端 + 原生 JS 单页界面
scripts/        素材生成、工程构建、CLI 交换、契约/重连、渲染验证、一键脚本
tests/          43 项 unittest（精确时间、往返、损失、逐帧、缺失媒体、双方向、
                能力契约、重连身份、随机顺序、校验器）
test_project/   测试工程、真实帧包素材、渲染产物
```

## 运行测试

```bash
python3 -I -m unittest discover -s tests -v
```
