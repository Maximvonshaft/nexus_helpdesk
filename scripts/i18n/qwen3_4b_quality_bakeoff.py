from __future__ import annotations

import qwen3_quality_bakeoff as bakeoff

# Qwen3-1.7B did not meet the reviewed German or Montenegrin quality bar.
# This isolated bake-off uses the stronger official instruct checkpoint while
# preserving the same corpus, repair loop, semantic contracts and evidence.
bakeoff.MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
bakeoff.REQUESTED_REVISION = None

if __name__ == "__main__":
    raise SystemExit(bakeoff.main())
