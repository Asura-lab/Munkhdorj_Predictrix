# Code Review: Thesis Security, Privacy, Trustworthiness
Date: 2026-04-16
Scope: diplom chapters + backend/mobile implementation evidence

Ready for Production: No (research prototype context)
Critical Issues: 2

## Priority 1 (Must Fix) ⛔

1) Private signal data exposure via public latest endpoint
- Evidence:
  - Private/manual records include user identifiers at backend/app.py:2632-2634.
  - Public endpoint returns auto + manual without auth gate at backend/app.py:2787-2831.
- Risk:
  - Potential leakage of submitted_by_email and user-linked trading activity.
- Recommended fix:
  - Restrict `/signals/latest` to trusted auto signals only, or require auth and enforce owner-only read for manual/private entries.
  - Add response projection to exclude submitted_by_* fields for any public response.

2) Password policy mismatch in reset/change paths
- Evidence:
  - Register enforces >=12 chars at backend/app.py:1473.
  - Reset/change currently enforce <6 check while error says 12 at backend/app.py:1835 and backend/app.py:1892.
- Risk:
  - Inconsistent effective policy can weaken account security and weakens documentation credibility.
- Recommended fix:
  - Enforce one shared constant (e.g., MIN_PASSWORD_LENGTH=12) across register/reset/change flows.

## Priority 2 (Should Fix)

1) Demo OTP exposure toggle exists
- Evidence: backend/app.py:1338, backend/app.py:1510, backend/app.py:1611, backend/app.py:1788.
- Risk: Misconfiguration can expose OTP in API responses.
- Fix: Hard-block in production runtime and add startup guard test.

2) CORS fallback includes development origins by default set
- Evidence: backend/app.py:127 with resolver logic at backend/app.py:113.
- Risk: Broader cross-origin attack surface than necessary.
- Fix: Require explicit CORS allowlist in production environment.

3) Registration endpoint reveals existing email
- Evidence: backend/app.py:1479.
- Risk: Account enumeration.
- Fix: Return generic registration response for both existing and new emails.

4) Missing explicit security headers policy in backend layer
- Evidence: no hardening middleware/header injection found in backend/app.py.
- Risk: Reduced defense-in-depth for browser clients.
- Fix: Add HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy.

5) Privacy governance in thesis is under-specified
- Evidence: trust/security claims in diplom/Chapters/Chapter1_Introduction.tex:12 and diplom/Chapters/Chapter3_Methodology.tex:765, but no quantified privacy controls chapter.
- Risk: Weak defense posture during thesis questioning.
- Fix: Add a compact control matrix (data class, storage, retention, access, deletion).

## Positive Controls Observed

1) JWT validation includes issuer/audience/type and required claims (backend/app.py:1240).
2) Refresh token hashing, storage, rotation, revocation implemented (backend/app.py:1132, backend/app.py:1168, backend/app.py:1214, backend/app.py:1704).
3) Auth and public endpoint rate limiting with Redis-backed option (backend/app.py:469).
4) Consent policy version checks and consent evidence capture implemented (backend/app.py:1387-1418).
5) Mobile token storage uses SecureStore-first strategy (mobile_app/src/services/authTokenStorage.ts:2, 24-44, 78-86).

## Thesis Documentation Quality Notes

1) Chapter 3 contains concrete statements that are now evidence-backed for token/session and worker reliability (diplom/Chapters/Chapter3_Methodology.tex:765).
2) Mobile stack table still states AsyncStorage for auth persistence (diplom/Chapters/Chapter3_Methodology.tex:802), while implementation moved to SecureStore-first (mobile_app/src/services/authTokenStorage.ts:2, 78-86). Update required.
3) Results chapter is strong on reproducibility of quantitative pipeline (diplom/Chapters/Chapter4_Results.tex:14, 101), but does not report security/privacy metrics.
# Code Review: Diplom Thesis Security, Privacy, Responsible Deployment Claims
**Review Date**: 2026-04-16  
**Scope**: Chapter1_Introduction.tex, Chapter3_Methodology.tex, Chapter4_Results.tex, Chapter5_Conclusion.tex, Abstract.tex  
**Ready for Production**: No (Documentation is incomplete for deployment governance)  
**Critical Issues**: 4

## Priority 1 (Must Fix) ⛔
- Privacy governance is not documented (PII definition, retention, deletion, lawful basis, user rights).
- API abuse controls are not documented (rate limit, anomaly detection, lockout, bot abuse handling).
- Responsible-use disclosure is incomplete ("not financial advice", user suitability, risk warning UX flow).
- Security controls are claimed but not evidenced (token policy, transport security, key management, incident response).

## Priority 2 (Should Fix)
- Authentication flow is described, but authorization model is unclear (RBAC, endpoint-level access policy).
- Operational monitoring is described at a high level, but no measurable SLO/SLA, alert thresholds, or runbook linkage is provided.
- Model governance is partially mentioned (drift, audit log in future work) but no deployment gate criteria are defined.
- Third-party data and AI provider risks are under-documented (dependency trust, outage handling, fallback behavior).

## Priority 3 (Nice to Have)
- Add a threat model figure (assets, trust boundaries, attacker goals).
- Add a data classification table (public/internal/confidential) and mapping to controls.

## Evidence Highlights
- Positive: authentication and short-lived token/session claims are documented in methodology.
- Positive: retry and background-job durability are documented.
- Positive: model risk caveats (backtest limitations, market regime change) are acknowledged in conclusion.
- Gap: secure backend integration is asserted in abstract without concrete protocol/control evidence.
- Gap: privacy and compliance terms are not materially covered in the reviewed chapters.

## Recommended Thesis Text Additions (Defense-Ready)
1. Add a "Security Control Matrix" subsection mapping controls to risks: authentication, authorization, transport security, secret handling, abuse prevention, audit logging.
2. Add a "Privacy & Data Governance" subsection: what user data is stored, retention period, deletion flow, access policy, third-party processors.
3. Add a "Responsible Deployment & User Protection" subsection: not-investment-advice notice, risk disclosure, confidence uncertainty disclaimer, user consent checkpoints.
4. Add an "Operations & Incident Response" subsection with monitoring KPIs, alert thresholds, escalation path, and recovery objectives.
5. Add a "Model Governance" subsection with drift trigger thresholds, rollback criteria, and human override policy.

## Suggested Acceptance Criteria Before Defense
- Each major security/privacy claim includes at least one verifiable artifact (table, metric, policy, or architecture detail).
- Claims in abstract and conclusion are traceable to a method/result section.
- User-facing risk disclosures are shown as concrete UX/content commitments, not only conceptual statements.