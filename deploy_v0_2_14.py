#!/usr/bin/env python3
"""Deploy v0.2.14 of hitron_coda_5610q to the HA Pi.

Stops HA, pushes all 9 integration files, clears the entity registry
of entities that no longer exist in v0.2.14 (DOCSIS sensors, pause/
resume buttons, per-channel sensors when diagnostics is off), then
restarts HA.
"""
import os, sys, subprocess, tempfile, time, base64, shlex

HA_USER = "hassio"; HA_PASS = "P0d1ch0nHA"
PI = f"{HA_USER}@192.168.0.43"
cdir = tempfile.mkdtemp()
ap = f"{cdir}/ap"
open(ap, "w").write(f"#!/bin/sh\necho {HA_PASS}\n"); os.chmod(ap, 0o755)
env = os.environ.copy()
env["SSH_ASKPASS"] = ap; env["SSH_ASKPASS_REQUIRE"] = "force"; env["DISPLAY"] = ":0"
base = ["ssh", "-tt", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-o", f"ControlPath={cdir}/c", "-o", "ControlMaster=auto", "-o", "ControlPersist=600", PI]
def ssh_run(cmd, timeout=120):
    r = subprocess.run(base + [cmd], env=env, capture_output=True, text=True, timeout=timeout)
    return r.stdout, r.stderr, r.returncode

INSTALL_DIR = "/config/custom_components/hitron_coda_5610q"
LOCAL_DIR = "/opt/data/hitron-coda-5610q/custom_components/hitron_coda_5610q"

# Files to deploy
FILES = [
    "manifest.json", "const.py", "binary_sensor.py", "button.py",
    "sensor.py", "config_flow.py", "__init__.py", "device_tracker.py",
    "services.yaml",
]

print("=== 1. Stop HA ===")
ssh_run("sudo docker stop homeassistant", timeout=30)
time.sleep(8)
ssh_run("sudo docker ps -a | grep homeassistant || echo 'not running'", timeout=15)

print("\n=== 2. Backup current v0.2.13 install ===")
ts = time.strftime("%Y%m%d_%H%M%S")
ssh_run(f"sudo mkdir -p /config/.hitron_backups/v0.2.13.{ts}", timeout=15)
out, err, rc = ssh_run(f"sudo cp -r {INSTALL_DIR} /config/.hitron_backups/v0.2.13.{ts}/", timeout=30)
print(f"  backup: {out.strip() or 'ok'}")

print("\n=== 3. Push v0.2.14 files ===")
for fn in FILES:
    local_path = os.path.join(LOCAL_DIR, fn)
    with open(local_path, 'rb') as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    # Push via chunks to avoid huge single-line commands
    chunks = [b64[i:i+4096] for i in range(0, len(b64), 4096)]
    # Decode to /tmp, then move into place
    ssh_run(f"echo -n '{chunks[0]}' | sudo tee /tmp/{fn}.b64 > /dev/null", timeout=15)
    for chunk in chunks[1:]:
        ssh_run(f"echo -n '{chunk}' | sudo tee -a /tmp/{fn}.b64 > /dev/null", timeout=15)
    out, err, rc = ssh_run(
        f"sudo bash -c 'base64 -d /tmp/{fn}.b64 > /tmp/{fn} && "
        f"rm -f /tmp/{fn}.b64 && "
        f"rm -rf {INSTALL_DIR}/__pycache__/{fn}.cpython-*.pyc 2>/dev/null; "
        f"mv /tmp/{fn} {INSTALL_DIR}/{fn} && "
        f"chown root:root {INSTALL_DIR}/{fn} && "
        f"echo OK_{fn}'",
        timeout=30
    )
    if f"OK_{fn}" in out:
        print(f"  {fn}: ok")
    else:
        print(f"  {fn}: FAILED")
        print(f"  stdout: {out}")
        print(f"  stderr: {err}")
        sys.exit(1)

print("\n=== 4. Verify version ===")
out, err, rc = ssh_run(f"sudo cat {INSTALL_DIR}/manifest.json | grep version")
print(f"  {out.strip()}")
if '"0.2.14"' not in out:
    print("  FAILED: wrong version"); sys.exit(1)

print("\n=== 5. Clear stale entity_registry entries ===")
# The 7 DOCSIS binary sensors, 42 buttons, and (default) per-channel
# sensors are no longer in v0.2.14. Remove them from the entity
# registry so HA doesn't keep showing "entity not available".
script = """
import json
with open('/config/.storage/core.entity_registry') as f:
    d = json.load(f)
removed = []
for e in d['data']['entities']:
    eid = e.get('entity_id', '')
    if e.get('platform') != 'hitron_coda_5610q':
        continue
    if eid.startswith('binary_sensor.hitron_coda_5610q_docsis_'):
        removed.append(eid)
    elif eid.startswith('button.hitron_coda_5610q_pause_') or eid.startswith('button.hitron_coda_5610q_resume_'):
        removed.append(eid)
    elif eid.startswith('sensor.hitron_coda_5610q_ds_channel_'):
        removed.append(eid)
    elif eid.startswith('sensor.hitron_coda_5610q_us_channel_'):
        removed.append(eid)
for eid in removed:
    for e in d['data']['entities']:
        if e.get('entity_id') == eid:
            d['data']['entities'].remove(e)
            break
with open('/config/.storage/core.entity_registry', 'w') as f:
    json.dump(d, f)
print(f'removed: {len(removed)}')
for eid in removed[:5]:
    print(f'  {eid}')
if len(removed) > 5:
    print(f'  ... and {len(removed) - 5} more')
"""
b64 = base64.b64encode(script.encode()).decode()
# Write b64 to a local file
b64file = f"{cdir}/clean.b64"
with open(b64file, 'w') as f: f.write(b64)
out, err, rc = ssh_run(f"cat {b64file} | sudo base64 -d > /home/hassio/clean.py && sudo docker cp /home/hassio/clean.py homeassistant:/tmp/clean.py && sudo docker exec homeassistant python3 /tmp/clean.py", timeout=60)
print(f"  {out.strip()}")

print("\n=== 6. Start HA ===")
ssh_run("sudo docker start homeassistant", timeout=30)
print("  Waiting 45s for setup...")
time.sleep(45)

print("\n=== 7. Verify integration is up ===")
out, err, rc = ssh_run("sudo docker logs --tail 50 homeassistant 2>&1 | grep -iE 'hitron_coda_5610q' | head -10")
print(out)
if 'v0.2.14' in out or 'first refresh OK' in out or 'forwards OK' in out:
    print("  Integration reloaded OK")
else:
    print("  WARNING: no clear success marker in logs")
print("\nDeploy done.")
