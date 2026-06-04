from __future__ import annotations

# Import-time installation for the PR381 v8 WebChat Fast API final guard.
# This patches app.api.webchat_fast._process_fast_reply before main.py includes
# the WebChat Fast router, so trusted KB direct_answer evidence can bypass the
# final server fallback / handoff side-effect path deterministically.
from . import webchat_fast_v8_patch as _webchat_fast_v8_patch  # noqa: F401
