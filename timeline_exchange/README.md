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
3. 页面并排显示**交换前 / 交换后（重新读取）**的编辑结构、导出文档原文、损失报告。
4. **拖动游标**，下方预览由剪辑交换内核返回该记录时刻实际选中的源帧（真实 PNG），
   并显示源帧号、源时间码、有理秒与转场进度；可切换“交换前 / 交换后”对照。
5. 内核失败（如文档解析错误）会在界面顶部红色回传，而不是吞掉。

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
web/            标准库 http.server 后端 + 原生 JS 单页界面
scripts/        素材生成、工程构建、CLI 交换、渲染验证、一键脚本
tests/          19 项 unittest（精确时间、往返、损失、逐帧、缺失媒体、双方向）
test_project/   测试工程、真实帧包素材、渲染产物
```

## 运行测试

```bash
python3 -I -m unittest discover -s tests -v
```
