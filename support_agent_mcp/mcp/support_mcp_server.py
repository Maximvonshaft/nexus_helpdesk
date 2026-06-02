import asyncio
import os
import sys
from typing import Optional
from types import SimpleNamespace

# Add the scripts directory to sys.path to import local modules
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.append(SCRIPTS_DIR)

import support_request_bus as srb
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("Speedaf Support Bus")

# NexusDesk Integration Settings (Deprecated/Removed)
# We now use Speedaf Work Order (WT0103-05) for all escalations.

@mcp.tool()
async def speedaf_lookup(tracking_number: str, debug: bool = False, source: str = "speedaf") -> str:
    """
    Look up a tracking number directly in the Speedaf system.
    Returns the latest tracking events, milestones, and AI empowerment summary.
    """
    args = SimpleNamespace(
        action='lookup',
        source=source,
        tracking_number=tracking_number,
        debug=debug,
        mock_status='',
        dry_run=False,
        language='chinese'
    )
    try:
        result = srb.speedaf_lookup(args)
        import json
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error in speedaf_lookup: {str(e)}"


@mcp.tool()
async def speedaf_update_address(
    tracking_number: str,
    whatsapp_phone: str,
    caller_id: str,
    debug: bool = False
) -> str:
    """
    Trigger an address update for a waybill in the Speedaf system.
    """
    import json
    
    # We must call the module directly, but it looks like we import support_request_bus as srb
    # Since we need speedaf_client, let's import it directly
    import speedaf_client
    
    try:
        res = speedaf_client.update_address(
            tracking_no=tracking_number,
            whatsapp_phone=whatsapp_phone,
            caller_id=caller_id,
            debug=debug
        )
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "ok": False,
            "error": "internal_error",
            "message": str(e)
        }, ensure_ascii=False)


@mcp.tool()
async def speedaf_cancel_order(tracking_number: str, reason_code: str, caller_id: str, debug: bool = False) -> str:
    """Cancel a waybill. Reason codes: CC01 (delay), CC02 (attitude), CC03 (no inspection), CC04 (no partial delivery), CC05 (other)"""
    import speedaf_client, json
    try:
        res = speedaf_client.cancel_order(tracking_number, reason_code, caller_id, debug=debug)
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

@mcp.tool()
async def speedaf_create_work_order(tracking_number: str, work_order_type: str, description: str, caller_id: str, debug: bool = False) -> str:
    """Create a work order in Speedaf system. Types: WT0103-05 (Urge dispatch/催派)"""
    import speedaf_client, json
    try:
        res = speedaf_client.create_work_order(tracking_number, work_order_type, description, caller_id, debug=debug)
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

@mcp.tool()
async def speedaf_query_waybills(caller_id: str, country_code: str, debug: bool = False) -> str:
    """Query tracking numbers by caller phone number and country code (e.g. NG, CN, CH)."""
    import speedaf_client, json
    try:
        res = speedaf_client.query_waybills(caller_id, country_code, debug=debug)
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})

if __name__ == "__main__":
    mcp.run()