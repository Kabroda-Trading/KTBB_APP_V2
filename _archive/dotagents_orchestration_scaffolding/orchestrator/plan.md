# Project Plan: Kabroda Diagnostic Command Center

This plan describes the orchestration topology for building the Kabroda diagnostic command center, implementing the dual-track system (Implementation Track + E2E Testing Track).

## Orchestration Topology
The top-level Project Orchestrator will spawn sub-orchestrators for parallel and sequential execution of the milestones.

### Track 1: Implementation
1. **sub_orch_m1** (Milestone M1: Dashboard Audit & Fix)
   - Scope: Audit `/suite/dashboard` and `/api/dashboard/*` and fix inconsistencies.
   - Outputs: Corrected calculations, chart rendering, and null-safety fixes.
   - Status: PLANNED (to run in parallel with M2 and M_TEST).
2. **sub_orch_m2** (Milestone M2: AI Diagnostic API)
   - Scope: Build the `/api/v1/system/*` JSON endpoints.
   - Outputs: State, trades, parameters, errors, and analysis endpoints.
   - Status: PLANNED (to run in parallel with M1 and M_TEST).
3. **sub_orch_m3** (Milestone M3: Upgraded Dashboard UI)
   - Scope: Redesign and upgrade dashboard UI with 5 tabs.
   - Dependencies: M1, M2.
   - Status: PLANNED (starts after M1 and M2 are completed).
4. **sub_orch_m4** (Milestone M4: AI Analysis Loop)
   - Scope: Implement periodic background analysis loop and report persistence.
   - Dependencies: M2.
   - Status: PLANNED (starts after M2 is completed).
5. **sub_orch_m5** (Milestone M5: Integration & Verification)
   - Scope: Final integration testing, verification against E2E test suite, and Tier 5 adversarial coverage hardening.
   - Dependencies: M3, M4, M_TEST.
   - Status: PLANNED.

### Track 2: E2E Testing
1. **sub_orch_test** (Milestone M_TEST: E2E Testing Track)
   - Scope: Build opaque-box E2E test suite from requirements.
   - Outputs: `TEST_INFRA.md`, E2E test scripts, and `TEST_READY.md`.
   - Status: PLANNED (runs in parallel with M1 and M2).
