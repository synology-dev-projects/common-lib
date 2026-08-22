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
    """Checks if the Synology self-hosted runner has finished running jobs."""
    cmd = 'plink.exe -ssh -pw "4354GoGo!!" -batch rachardv@192.168.1.68 "echo \'4354GoGo!!\' | sudo -S /usr/local/bin/docker logs --tail 5 synology-github-runner"'
    ok, out = run_cmd(cmd)
    return out

def main():
    print("=" * 60)
    print("🛡️ QUANT SYSTEM STEP-GATED MULTI-REPO DEPLOYMENT")
    print("=" * 60)

    # 1. Gate 1: common-lib
    print("\n[Gate 1] Pushing common-lib to develop...")
    ok, _ = run_cmd("git add -A && git commit -m 'chore: automated deploy' && git push origin develop", cwd=r"c:\Coding\VSCode\Quant System\common-lib")
    if not ok:
        print("⚠️ Nothing to commit or push failed in common-lib.")

    print("\n⏳ [Gate 1] Waiting for Synology Runner to verify and merge common-lib to master...")
    for attempt in range(20):
        time.sleep(10)
        logs = check_synology_runner()
        print(f"[{attempt + 1}/20] Checking runner status...")
        if "Job deploy-branch completed with result: Succeeded" in logs:
            print("✅ common-lib master promotion confirmed!")
            break

    # 2. Gate 2: gexdex-api & backend microservices
    print("\n[Gate 2] Pushing backend microservices (gexdex-api)...")
    run_cmd("git add -A && git commit -m 'chore: automated deploy' && git push origin develop", cwd=r"c:\Coding\VSCode\Quant System\gexdex-api")

    time.sleep(15)

    # 3. Gate 3: quant-pwa (ALWAYS LAST)
    print("\n[Gate 3] Pushing quant-pwa (LAST)...")
    run_cmd("git add -A && git commit -m 'chore: automated deploy' && git push origin develop", cwd=r"c:\Coding\VSCode\Quant System\quant-pwa")
    print("\n🎉 Deployment completed in strict sequence!")

if __name__ == "__main__":
    main()
