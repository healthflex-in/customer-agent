# Customer Agent Documentation

This directory documents the current Stance Health patient intake application.
It does not describe a clinician agent, a Whisper/VAD prototype, or a standalone
question-pool orchestrator; those were historical concepts that are not the
active runtime.

Start here:

- [Current REST and WebSocket API](PUBLIC_API.md)
- [Local development](LOCAL_DEV.md)
- [Deployment artifact status](DEPLOYMENT.md)
- [Isolated development deployment on a shared server](DEV_ISOLATED_DEPLOYMENT.md)

The active application is a React patient interface backed by FastAPI,
LangGraph, Gemini, MongoDB, optional Google Speech fallback, and an optional
S3/Bedrock report pipeline. The root [README](../../README.md) provides the short
project entry point.

Documentation changes should be verified with:

```bash
cd DataHandling
python3 -m unittest tests.test_documentation_contract
```
