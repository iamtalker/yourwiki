"""시험 모두 돌리기: python3 tests/run_all.py  (바깥 인터넷 없이 로컬에서만 돈다)"""
import glob
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
bad = 0
for t in sorted(glob.glob(os.path.join(here, "test_*.py"))):
    r = subprocess.run([sys.executable, t], timeout=300)
    bad += r.returncode != 0
    if r.returncode:
        print(f"실패: {os.path.basename(t)}")
print("모두 통과" if not bad else f"{bad}개 실패")
sys.exit(1 if bad else 0)
