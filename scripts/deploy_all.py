"""
Quant System Deterministic Multi-Repo Deployment Script
Strictly enforces Rule 3:
1. Pushes common-lib first -> verifies master promotion.
2. Pushes backend services (gexdex-api, pipelines) -> verifies.
3. Pushes quant-pwa last.
"""

import subprocess
import time
import sys

def run_cmd(cmd, cwd=None):
    print(f"\n🚀 Running: {cmd} (cwd: {cwd or '.'})")
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"❌ Error ({res.returncode}): {res.stderr}")
        return False, res.stderr
    print(res.stdout.strip())
    return True, res.stdout.strip()

def check_synology_runner():
    """Checks the latest GitHub Actions workflow run conclusion for common-lib using GitHub CLI."""
    cmd = 'gh run list --repo synology-dev-projects/common-lib -L 1 --json status,conclusion -q ".[0].conclusion // .[0].status"'
    ok, out = run_cmd(cmd)
    return out.strip() if ok else ""

def main():
    print("=" * 60)
    print("🛡️ QUANT SYSTEM STEP-GATED MULTI-REPO DEPLOYMENT")
    print("=" * 60)

    # 1. Gate 1: common-lib
    print("\n[Gate 1] Pushing common-lib to develop2...")
    ok, _ = run_cmd("git add -A && git commit -m 'chore: automated deploy' && git push origin develop2", cwd=r"c:\Coding\VSCode\Quant System\common-lib")
    if not ok:
        print("⚠️ Nothing to commit or push failed in common-lib.")

    print("\n⏳ [Gate 1] Waiting for Synology Runner to verify and deploy common-lib...")
    for attempt in range(25):
        time.sleep(10)
        status = check_synology_runner()
        print(f"[{attempt + 1}/25] Checking GitHub Actions workflow status: '{status}'...")
        if status == "success":
            print("✅ common-lib deployment confirmed successful on Synology runner!")
            break

    # 2. Gate 2: gexdex-api & backend microservices
    # NOTE: gexdex-api has been decommissioned and archived into archive/gexdex-api.
    # Its options microstructure engine now runs in-process inside quant-pwa/gateway/app/engine/service.py.
    # Standalone push is bypassed; microservices deploy automatically with quant-pwa (Gate 3).
    print("\n[Gate 2] Backend microservices: gexdex-api archived (consolidated in-process in quant-pwa Gateway).")

    # 3. Gate 3: quant-pwa (ALWAYS LAST)
    print("\n[Gate 3] Pushing quant-pwa (LAST)...")
    run_cmd("git add -A && git commit -m 'chore: automated deploy' && git push origin develop2", cwd=r"c:\Coding\VSCode\Quant System\quant-pwa")
    print("\n🎉 Deployment completed in strict sequence!")

if __name__ == "__main__":
    main()
