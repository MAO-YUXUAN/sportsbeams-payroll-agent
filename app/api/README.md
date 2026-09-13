# Sportsbeams Payroll Agent API

Start locally with `powershell -ExecutionPolicy Bypass -File launcher/start_backend.ps1`.
The service listens only on `127.0.0.1:8765`. Interactive OpenAPI documentation is
available at `http://127.0.0.1:8765/docs`.

Typical client flow:

1. `POST /api/v1/uploads` with the payroll month.
2. Upload each workbook to `POST /api/v1/uploads/{upload_id}/files`.
3. `POST /api/v1/runs` to create an isolated payroll run.
4. `POST /api/v1/runs/{run_id}/advance`, then poll `/api/v1/jobs/{job_id}`.
5. Read and decide approvals under `/api/v1/runs/{run_id}/approvals`.
6. Repeat advance/approval until completed, then list and download outputs.

The API never returns an API key. Excel work runs are serialized because Microsoft
Excel COM automation is not safe for concurrent workbook mutation.
