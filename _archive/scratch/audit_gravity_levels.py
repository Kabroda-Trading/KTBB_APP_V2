"""Audit the gravity map levels — are they real or noise?"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, GravityMemory
from datetime import datetime, timezone
from collections import Counter

db = SessionLocal()
try:
    levels = db.query(GravityMemory).filter(
        GravityMemory.active == True
    ).order_by(GravityMemory.price).all()

    print(f"Total active levels: {len(levels)}")
    print(f"Price range: ${min(l.price for l in levels):>10,.2f} — ${max(l.price for l in levels):>10,.2f}")
    print()

    # By permanence class
    pc_counts = Counter(l.permanence_class for l in levels)
    print("=== BY PERMANENCE CLASS ===")
    for pc in sorted(pc_counts.keys()):
        print(f"  Class {pc}: {pc_counts[pc]} levels")
    print()

    # By source
    src_counts = Counter(l.source for l in levels)
    print("=== BY SOURCE ===")
    for src, count in src_counts.most_common():
        print(f"  {src}: {count}")
    print()

    # By level type
    type_counts = Counter(l.level_type for l in levels)
    print("=== BY LEVEL TYPE ===")
    for t, count in type_counts.most_common():
        print(f"  {t}: {count}")
    print()

    # Class 0 (macro beams) — these are the most important
    class0 = [l for l in levels if l.permanence_class == 0]
    print(f"=== CLASS 0 MACRO BEAMS ({len(class0)}) ===")
    for l in sorted(class0, key=lambda x: x.price):
        print(f"  ${l.price:>10,.2f} | {l.level_type:30s} | source: {l.source:20s} | heat: {l.heat_multiplier:5.1f} | touches: {l.touch_count}")
    print()

    # Class 1 (4H guardrails)
    class1 = [l for l in levels if l.permanence_class == 1]
    print(f"=== CLASS 1 (4H GUARDRAILS) ({len(class1)}) ===")
    for l in sorted(class1, key=lambda x: x.price):
        print(f"  ${l.price:>10,.2f} | {l.level_type:30s} | source: {l.source:20s} | heat: {l.heat_multiplier:5.1f} | touches: {l.touch_count}")
    print()

    # Levels with touch_count > 0 (price has revisited them)
    touched = [l for l in levels if l.touch_count > 0]
    print(f"=== LEVELS WITH TOUCHES ({len(touched)}) ===")
    for l in sorted(touched, key=lambda x: x.touch_count, reverse=True)[:20]:
        print(f"  ${l.price:>10,.2f} | touches: {l.touch_count:3d} | {l.level_type:30s} | class: {l.permanence_class} | source: {l.source}")
    print()

    # Levels with accuracy data
    with_accuracy = [l for l in levels if (l.accuracy_hits or 0) + (l.accuracy_breaks or 0) > 0]
    print(f"=== LEVELS WITH ACCURACY DATA ({len(with_accuracy)}) ===")
    for l in sorted(with_accuracy, key=lambda x: x.accuracy_score or 0, reverse=True)[:20]:
        print(f"  ${l.price:>10,.2f} | hits: {l.accuracy_hits:3d} | breaks: {l.accuracy_breaks:3d} | score: {l.accuracy_score:5.1f}% | {l.level_type:30s} | class: {l.permanence_class}")
    print()

    # Levels with departure_move_pct (how far price moved after forming)
    departed = [l for l in levels if l.departure_move_pct is not None]
    print(f"=== LEVELS WITH DEPARTURE DATA ({len(departed)}) ===")
    for l in sorted(departed, key=lambda x: abs(x.departure_move_pct or 0), reverse=True)[:10]:
        print(f"  ${l.price:>10,.2f} | departure: {l.departure_move_pct:+.2f}% | {l.level_type:30s} | class: {l.permanence_class}")
    print()

    # Levels with NO touches, NO departure data, NO accuracy data — pure noise candidates
    noise = [l for l in levels if l.touch_count == 0 and l.departure_move_pct is None and (l.accuracy_hits or 0) == 0 and (l.accuracy_breaks or 0) == 0]
    print(f"=== NOISE CANDIDATES (no data at all) ({len(noise)}) ===")
    for l in sorted(noise, key=lambda x: x.price)[:20]:
        print(f"  ${l.price:>10,.2f} | {l.level_type:30s} | class: {l.permanence_class} | source: {l.source}")
    if len(noise) > 20:
        print(f"  ... and {len(noise) - 20} more")
    print()

    # Summary
    print("=== SUMMARY ===")
    print(f"Total: {len(levels)}")
    print(f"Class 0 (macro): {len(class0)}")
    print(f"Class 1 (guardrails): {len(class1)}")
    print(f"Other: {len(levels) - len(class0) - len(class1)}")
    print(f"With touches: {len(touched)} ({len(touched)/len(levels)*100:.1f}%)")
    print(f"With accuracy data: {len(with_accuracy)} ({len(with_accuracy)/len(levels)*100:.1f}%)")
    print(f"Noise candidates: {len(noise)} ({len(noise)/len(levels)*100:.1f}%)")

finally:
    db.close()
