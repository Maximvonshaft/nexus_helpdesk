# AI Resource Server Capability Probe

See `scripts/ai/probe_ai_resource_server.py` and the previous version of this document for the full operator guide.

The probe is read-only by default, contacts only operator-declared URLs, does not invoke RAG ingestion or business writes, and does not retain tokens, raw provider bodies, prompts, transcripts or generated audio.

Run the complete example:

```bash
cp config/ai-resource-probe.example.json /tmp/ai-resource-probe.json
python scripts/ai/probe_ai_resource_server.py --config /tmp/ai-resource-probe.json --output artifacts/ai-resource-probe.json --pretty
```

Do not place credentials in HTTP or WebSocket URLs. Use environment variables or read-only secret files.
