# Hidden Tower Defence

Hidden Tower Defence is a secure Hacker News developer-community intelligence
demo. It treats incoming content as untrusted, scans every agent and tool
boundary, turns trustworthy content into structured intelligence, and renders
the process as a pixel-art castle.

The authoritative product and delivery design is
[`END_TO_END_IMPLEMENTATION_PLAN.md`](END_TO_END_IMPLEMENTATION_PLAN.md).

## Local startup

```bash
python3 -m pip install -e ".[dev]"
python3 -m uvicorn app.main:app --reload --port 8080
```

Open <http://localhost:8080>. Without external credentials, the controlled
fixtures still demonstrate clean, restricted, and locked policy paths.

## Production architecture

- Cloud Run in `smp-shared-prod`
- Cloud Spanner database `hiddentowerdefence` in the shared instance
- Secret Manager for runtime provider credentials
- Artifact Registry digest-pinned images
- GitHub Actions authenticated by Workload Identity Federation

The existing `run.app` hostname is used before domain delegation completes.
The planned production domain is `hiddentowerdefence.com`.

## Sponsor technology rationale

- Apify supplies bounded, repeatable Hacker News ingestion.
- HiddenLayer protects public-source content, model prompts and responses, and
  tool inputs and results.
- NVIDIA Nemotron powers the Claw intelligence agent.
- Google Cloud Run and Spanner provide managed deployment and durable state.
