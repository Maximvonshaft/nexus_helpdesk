import json

class OutputContracts:
    @staticmethod
    def get_schema(contract_name: str) -> dict:
        if contract_name == "speedaf_webchat_fast_reply_v1":
            return {
                "type": "object",
                "properties": {
                    "customer_reply": {"type": "string"},
                    "language": {"type": "string"},
                    "intent": {"type": "string", "enum": ["greeting", "tracking", "tracking_missing_number", "tracking_unresolved", "complaint", "address_change", "handoff", "other"]},
                    "tracking_number": {"type": ["string", "null"]},
                    "handoff_required": {"type": "boolean"},
                    "handoff_reason": {"type": ["string", "null"]},
                    "recommended_agent_action": {"type": ["string", "null"]},
                    "ticket_should_create": {"type": "boolean"},
                    "internal_summary": {"type": ["string", "null"]},
                    "risk_flags": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["customer_reply", "language", "intent", "handoff_required", "ticket_should_create"]
            }
        elif contract_name == "speedaf_ticket_triage_v1":
            return {
                "type": "object",
                "properties": {
                    "ticket_title": {"type": "string"},
                    "ticket_category": {"type": "string", "enum": ["delivery_exception", "tracking", "complaint", "address_change", "claim", "other"]},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                    "customer_reply": {"type": "string"},
                    "agent_brief": {"type": "string"},
                    "required_human_action": {"type": ["string", "null"]},
                    "evidence_needed": {"type": "array", "items": {"type": "string"}},
                    "handoff_required": {"type": "boolean"}
                },
                "required": ["ticket_title", "ticket_category", "priority", "customer_reply", "agent_brief", "handoff_required", "evidence_needed"]
            }
        elif contract_name == "speedaf_delivery_exception_analysis_v1":
            return {
                "type": "object",
                "properties": {
                    "exception_type": {"type": "string", "enum": ["failed_delivery", "delivered_not_received", "wrong_address", "customs", "damaged", "lost", "other"]},
                    "root_cause_guess": {"type": ["string", "null"]},
                    "next_action": {"type": "string", "enum": ["reattempt", "investigate", "return", "manual_review", "none"]},
                    "customer_visible_reply": {"type": "string"},
                    "internal_action_required": {"type": "boolean"},
                    "evidence_needed": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["exception_type", "next_action", "customer_visible_reply", "internal_action_required", "evidence_needed"]
            }
        return {}

    @staticmethod
    def validate_and_parse(contract_name: str, raw_output: str) -> dict:
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError:
            raise ValueError("Output must be valid JSON")
            
        schema = OutputContracts.get_schema(contract_name)
        if not schema:
            return parsed
            
        for req in schema.get("required", []):
            if req not in parsed:
                raise ValueError(f"Missing required field: {req}")
                
        return parsed
