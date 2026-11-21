#!/usr/bin/env bash
# End-to-end convenience runner: regenerate media, project, tests and renders.
# Uses `python3 -I` so imported modules come only from this repository.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1/6 生成真实帧包素材 + 媒体目录索引 (ImageMagick) =="
python3 -I scripts/generate_media.py

echo "== 2/6 构建混合帧率测试工程 =="
python3 -I scripts/build_test_project.py

echo "== 3/6 运行内核测试套件 =="
python3 -I -m unittest discover -s tests -v

echo "== 4/6 运行能力契约与场景，冻结实测结果 =="
python3 -I scripts/run_contracts.py --seed 7

echo "== 5/6 用真实素材重连并校验源引用 =="
python3 -I scripts/run_relink.py --seed 7 --out test_project/contracts/relink.json

echo "== 6/6 渲染交换前后联系表并逐帧审计 =="
python3 -I scripts/render_verify.py

echo
echo "全部完成。启动界面："
echo "  python3 -I web/server.py --port 8765"
echo "然后浏览器打开 http://127.0.0.1:8765/"
