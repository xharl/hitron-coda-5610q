#!/usr/bin/env python3
"""Post-deploy fixes:
  1. Push the fixed __init__.py (with Platform.BUTTON removed)
  2. Purge __pycache__
  3. Run entity_registry cleanup
  4. Restart HA
  5. Verify
"""
import os, sys, subprocess, tempfile, time, base64

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

PI_INSTALL = "/config/custom_components/hitron_coda_5610q"
LOCAL_INSTALL = "/opt/data/hitron-coda-5610q/custom_components/hitron_coda_5610q"

# 1. Stop HA
print("=== Stop HA ===")
ssh_run("sudo docker stop homeassistant", timeout=30); time.sleep(8)

# 2. Push fixed __init__.py
print("=== Push fixed __init__.py ===")
init_local = LOCAL_INSTALL + "/__init__.py"
with open(init_local, 'rb') as f:
    data = f.read()
b64 = base64.b64encode(data).decode()
chunks = [b64[i:i+4096] for i in range(0, len(b64), 4096)]
ssh_run(f"echo -n '{chunks[0]}' | sudo tee /tmp/init.b64 > /dev/null", timeout=15)
for chunk in chunks[1:]:
    ssh_run(f"echo -n '{chunk}' | sudo tee -a /tmp/init.b64 > /dev/null", timeout=15)
out, err, rc = ssh_run(
    "sudo bash -c 'base64 -d /tmp/init.b64 > /tmp/__init__.py && "
    "rm -f /tmp/init.b64 && "
    "rm -rf " + PI_INSTALL + "/__pycache__ && "
    "mv /tmp/__init__.py " + PI_INSTALL + "/__init__.py && "
    "chown root:root " + PI_INSTALL + "/__init__.py && "
    "echo OK'"
)
print(f"  {out.strip() or out.strip() or 'ok'}")

# 3. Run entity_registry cleanup (push the script via base64 file to a path that's accessible)
print("\n=== Clean up stale entity_registry entries ===")
script = '''
import json
with open('/config/.storage/core.entity_registry') as f:
    d = json.load(f)
removed = []
for e in list(d['data']['entities']):
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
'''
# Use SCP to push the script directly
script_local = f"{cdir}/clean.py"
with open(script_local, 'w') as f:
    f.write(script)
# Use scp to push, then docker cp
scp_proc = subprocess.run(
    ["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
     "-o", f"ControlPath={cdir}/c", script_local, f"{PI}:/home/hassio/clean.py"],
    env=env, capture_output=True, text=True, timeout=30
)
if scp_proc.returncode != 0:
    print(f"  scp failed: {scp_proc.stderr}")
    # Fall back: push via ssh + base64 chunk
    b64 = base64.b64encode(script.encode()).decode()
    chunks = [b64[i:i+4096] for i in range(0, len(b64), 4096)]
    ssh_run(f"echo -n '{chunks[0]}' | sudo tee /tmp/clean.b64 > /dev/null", timeout=15)
    for chunk in chunks[1:]:
        ssh_run(f"echo -n '{chunk}' | sudo tee -a /tmp/clean.b64 > /dev/null", timeout=15)
    ssh_run("sudo base64 -d /tmp/clean.b64 > /home/hassio/clean.py && sudo rm -f /tmp/clean.b64", timeout=15)
    ssh_run("sudo docker cp /home/hassio/clean.py homeassistant:/tmp/clean.py", timeout=15)
else:
    ssh_run("sudo docker cp /home/hassio/clean.py homeassistant:/tmp/clean.py", timeout=15)
out, err, rc = ssh_run("sudo docker exec homeassistant python3 /tmp/clean.py", timeout=30)
print(f"  {out.strip()}")

# 4. Start HA
print("\n=== Start HA ===")
ssh_run("sudo docker start homeassistant", timeout=30)
print("Waiting 60s for full setup...")
time.sleep(60)

# 5. Verify
print("\n=== Verify ===")
out, err, rc = ssh_run("sudo docker logs --tail 50 homeassistant 2>&1 | grep -iE 'hitron_coda_5610q' | tail -10")
print(out)
if 'async_setup_entry' in out and 'no attribute' in out:
    print("FAILED: still has async_setup_entry error")
elif 'first refresh OK' in out or 'forwards OK' in out:
    print("OK: integration loaded")
