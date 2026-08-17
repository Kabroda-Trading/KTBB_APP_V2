#!/usr/bin/env python3
"""Quick script to run the system auditor and print a summary."""
import sys
sys.path.insert(0, r'C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\bold-hubble')
from kqal.system_auditor import audit_system
import json

report = audit_system()

print("=" * 60)
print(f"KQAL SYSTEM HEALTH REPORT")
print(f"Overall Health: {report.get('overall_health', 'N/A')}%")
print("=" * 60)

print("\n--- INDICATORS ---")
for k, v in report.get('indicators', {}).items():
    print(f"  {k}: {v['status']}")

print("\n--- STRATEGIES ---")
for k, v in report.get('strategies', {}).items():
    print(f"  {k}: {v['status']}")

print("\n--- MISSING COMPONENTS ---")
for m in report.get('missing_components', []):
    print(f"  {m['name']} ({m['priority']})")

print("\n--- PARAMETER MISMATCHES ---")
for m in report.get('parameter_mismatches', []):
    name = m.get('indicator', m.get('strategy', '?'))
    print(f"  {name}: {m['parameter']} ref={m['reference']} actual={m['actual']} ({m['severity']})")

print("\n--- CORRECTIONS ---")
for c in report.get('corrections', []):
    print(f"  {c['id']}: {c['title']} [{c['effort']}/{c['impact']}]")
