# Test Readiness Status

This document contains instructions on how to run the E2E test suite and the current test execution metrics.

## Test Runner Command

To execute the opaque-box E2E test suite on the Kabroda Diagnostic Command Center, run the following command from the project root:

```bash
python -m unittest tests/test_e2e.py
```

## Current Test Metrics (Initial Status)

- **Total Test Cases**: 83
- **Expected Success Status**: 100% execution coverage.
- **Expected Passing (un-upgraded codebase)**: 15 / 83 cases (18.1%)
- **Expected Failing (un-upgraded codebase)**: 68 / 83 cases (81.9%)
- **Target Coverage**: 0% passing / 83 failing for the newly planned API endpoints and upgraded UI features.

*Note: The test suite runs against the un-upgraded codebase. Failures (such as 404 Not Found on the newly introduced `/api/v1/system/*` routes) are normal and expected until the corresponding implementation track tasks (M1-M4) are completed.*
