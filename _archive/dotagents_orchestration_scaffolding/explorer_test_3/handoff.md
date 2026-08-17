# E2E Test Plan Design Handoff Report

## 1. Observation
* **Database URL Resolution**:
  In `database.py` line 7:
  ```python
  DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kabroda.db")
  ```
  And lines 27-28:
  ```python
  def init_db():
      Base.metadata.create_all(bind=engine)
  ```
  This indicates database redirection and initialization can be controlled strictly via the `DATABASE_URL` environment variable.

* **Authentication & Bootstrap**:
  In `auth.py` lines 14, 35-38, and 40-55:
  ```python
  SESSION_KEY = "kabroda_user_id"
  ...
  def require_session_user(request: Request) -> int:
      user_id = request.session.get(SESSION_KEY)
      if not user_id: raise HTTPException(status_code=401, detail="Not authenticated")
      return int(user_id)
  ...
  def ensure_bootstrap_admin(db: Session) -> None:
      admin_email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
      admin_password = os.getenv("ADMIN_PASSWORD") or ""
      ...
  ```
  Authentication relies on signed session cookies via Starlette middleware, and admin users are bootstrapped on demand during login actions if `ADMIN_EMAIL`/`ADMIN_PASSWORD` env vars are set.

* **Session Middleware Configuration**:
  In `main.py` lines 658-664:
  ```python
  app.add_middleware(
      SessionMiddleware,
      secret_key=SECRET_KEY,
      https_only=SESSION_HTTPS_ONLY,
      same_site="lax",
      max_age=86400 * 30  
  )
  ```
  If testing locally, we must set `SESSION_HTTPS_ONLY=False` or `PUBLIC_BASE_URL` without `https://` to avoid the browser dropping the session cookie.

* **Status of `/api/v1/system/*` Endpoints**:
  Verified via Python search script that the string `/api/v1/system` does not yet exist in any `.py` file, indicating that these endpoints are currently in development under the M2 milestone:
  ```powershell
  # Python search completed successfully with no output
  ```

* **Project Architecture and Scope**:
  `PROJECT.md` and `SCOPE.md` outline the 7 target features (F1: `/state`, F2: `/trades`, F3: `/parameters`, F4: `/errors`, F5: `/analysis`, F6: UI dashboard, F7: background analysis loop) and 4 testing tiers.

---

## 2. Logic Chain
1. Since `DATABASE_URL` defaults to `sqlite:///./kabroda.db` but checks the environment, setting `DATABASE_URL=sqlite:///./kabroda_test.db` will completely isolate the database state.
2. Since `init_db()` creates all tables, importing it and running it at test startup initializes an empty schema in `kabroda_test.db`.
3. Since uvicorn runs in a spawned subprocess, it must be started with the same environment variables (`DATABASE_URL=sqlite:///./kabroda_test.db`, `ADMIN_EMAIL=...`, `ADMIN_PASSWORD=...`) to ensure it talks to the isolated test database and boots up the test credentials.
4. Since `ensure_bootstrap_admin` bootstraps the admin user on `POST /login`, calling `POST /login` with `ADMIN_EMAIL` and `ADMIN_PASSWORD` is sufficient to seed and authenticate a new admin session.
5. In addition, seed data (for campaigns, narrative logs, errors) can be directly written to `kabroda_test.db` from the test runner prior to launching uvicorn.
6. A test port can be dynamically allocated, and we must wait (up to 5s) for the server to bind before executing tests to avoid connection errors.
7. To fully cover the 7 features specified in `SCOPE.md` across 4 tiers, we need at least 35 feature happy-path cases (Tier 1), 35 edge/boundary cases (Tier 2), 7 cross-feature interaction cases (Tier 3), and 5 real-world scenario cases (Tier 4), totaling 82 test cases.

---

## 3. Caveats
* **PostgreSQL vs SQLite Divergence**:
  While testing uses `sqlite:///./kabroda_test.db` for local isolation, the production server might use PostgreSQL (as shown by postgres replacement logic in `database.py`). E2E tests using SQLite may not catch PostgreSQL-specific database anomalies.
* **API Implementation Pending**:
  The E2E tests cannot be run successfully yet because the `/api/v1/system/*` endpoints are not implemented. The tests must be built against the specifications defined in `PROJECT.md` and `SCOPE.md`.

---

## 4. Conclusion
We have successfully designed a clean, isolated, opaque-box E2E test plan. The design runs the FastAPI app in a subprocess using a temporary SQLite test database, bootstraps admin authentication, and defines 82+ structured test cases spanning 4 tiers. The detailed test case specifications are saved in `analysis.md`.

---

## 5. Verification Method
1. Inspect `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\.agents\explorer_test_3\analysis.md` to verify the detailed 82+ test case descriptions.
2. Confirm the test design adheres to the scope by reviewing `C:\Users\Shadow\OneDrive\Desktop\KTBB_app_v2\.agents\sub_orch_test\SCOPE.md`.
