import json
import jsonschema

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
                "required": ["customer_reply", "language", "intent", "handoff_required", "ticket_should_create"],
                "additionalProperties": False
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
                "required": ["ticket_title", "ticket_category", "priority", "customer_reply", "agent_brief", "handoff_required", "evidence_needed"],
                "additionalProperties": False
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
                "required": ["exception_type", "next_action", "customer_visible_reply", "internal_action_required", "evidence_needed"],
                "additionalProperties": False
            }
        return {}

    @staticmethod
    def check_security_rules(raw_output: str, parsed: dict, check_tracking_fact: bool = False):
        lower_raw = raw_output.lower()
        
        # 1. No markdown allowed
        if "```" in raw_output:
            raise ValueError("Markdown code blocks are prohibited")
            
        # 2. No hidden reasoning
        if "<think>" in lower_raw:
            raise ValueError("Hidden reasoning tags are prohibited")
            
        # 3. No token leakage
        if "sk-" in lower_raw or "ey" in lower_raw: # Basic heuristic
            # Need to be careful with 'ey' since it's a common English syllable (e.g., 'they', 'key', 'hey').
            # Let's be more specific: "eyJ" is the start of a JWT.
            if "sk-" in lower_raw or "eyj" in lower_raw:
                raise ValueError("Potential token leakage detected")
                
        # 4. No internal network references
        if "localhost" in lower_raw or "127.0.0.1" in lower_raw or "bridge" in lower_raw:
            raise ValueError("Internal network references are prohibited")
            
        # 5. Tracking fact rules
        if parsed.get("intent") == "tracking":
            if not parsed.get("tracking_number"):
                raise ValueError("Tracking intent without a tracking_number is prohibited")
            if not check_tracking_fact:
                # If we don't have tracking facts present but intent is tracking, the model shouldn't invent status
                # We can flag this if required. For now, strict parser validates structure.
                pass

    @staticmethod
    def validate_and_parse(contract_name: str, raw_output: str, evidence_present: bool = False) -> dict:
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError:
            raise ValueError("Output must be valid JSON")
            
        schema = OutputContracts.get_schema(contract_name)
        if schema:
            try:
                jsonschema.validate(instance=parsed, schema=schema)
            except jsonschema.exceptions.ValidationError as e:
                raise ValueError(f"Schema validation failed: {e.message}")
                
        OutputContracts.check_security_rules(raw_output, parsed, check_tracking_fact=evidence_present)
        return parsed
