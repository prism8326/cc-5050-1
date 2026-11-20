#!/usr/bin/env bash
# End-to-end convenience runner: regenerate media, project, tests and renders.
# Uses `python3 -I` so imported modules come only from this repository.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1/4 生成真实帧包素材 (ImageMagick) =="
python3 -I scripts/generate_media.py

echo "== 2/4 构建混合帧率测试工程 =="
python3 -I scripts/build_test_project.py

echo "== 3/4 运行内核测试套件 =="
python3 -I -m unittest discover -s tests -v

echo "== 4/4 渲染交换前后联系表并逐帧审计 =="
python3 -I scripts/render_verify.py

echo
echo "全部完成。启动界面："
echo "  python3 -I web/server.py --port 8765"
echo "然后浏览器打开 http://127.0.0.1:8765/"
