from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth_service import hash_password
from app.db import Base, SessionLocal, engine
from app.enums import TicketPriority, UserRole
from app.models import AIConfigResource, Customer, Market, MarketBulletin, Tag, Team, User
from app.schemas import CustomerInput, TicketCreate
from app.services.sla_service import seed_default_sla_policies
from app.services.ai_config_service import publish_resource
from app.services.ticket_service import create_ticket
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: F401 - ensure metadata registration


def seed_data() -> None:
    db = SessionLocal()
    try:
        seed_default_sla_policies(db)
        if not db.query(Team).count():
            db.add_all([
                Team(name='Support', team_type='support'),
                Team(name='Escalations', team_type='escalation'),
                Team(name='Operations', team_type='ops'),
            ])
            db.commit()
        teams = {t.name: t for t in db.query(Team).all()}
        if not db.query(User).count():
            db.add_all([
                User(username='admin', display_name='Admin User', email='admin@speedaf.local', password_hash=hash_password('demo123'), role=UserRole.admin, team_id=teams['Support'].id),
                User(username='lead', display_name='Lead One', email='lead@speedaf.local', password_hash=hash_password('demo123'), role=UserRole.lead, team_id=teams['Support'].id),
                User(username='agent', display_name='Agent One', email='agent@speedaf.local', password_hash=hash_password('demo123'), role=UserRole.agent, team_id=teams['Support'].id),
                User(username='ops', display_name='Ops One', email='ops@speedaf.local', password_hash=hash_password('demo123'), role=UserRole.manager, team_id=teams['Operations'].id),
            ])
            db.commit()
        if not db.query(Tag).count():
            db.add_all([Tag(name='VIP', color='#f28a1a'), Tag(name='Complaint', color='#d9534f'), Tag(name='Delay', color='#ffb74d')])
            db.commit()
        if not db.query(Customer).count():
            customer = Customer(name='Alice Brown', email='alice-brown@email.com', email_normalized='alice-brown@email.com', phone='+12345678901', phone_normalized='+12345678901')
            db.add(customer)
            db.commit()
        if not db.query(Market).count():
            db.add_all([
                Market(code='PH', name='Philippines', country_code='PH'),
                Market(code='CH', name='Switzerland', country_code='CH'),
            ])
            db.commit()
        markets = {m.code: m for m in db.query(Market).all()}
        if not db.query(Customer).join(Customer.tickets).count():
            creator = db.query(User).filter(User.username == 'lead').first()
            create_ticket(
                db,
                TicketCreate(
                    title='Customer asks for delivery update',
                    description='Parcel has not arrived and customer is worried about the delay.',
                    source='ai_intake',
                    source_channel='whatsapp',
                    priority=TicketPriority.high,
                    category='delivery',
                    sub_category='delay',
                    tracking_number='LB123456789',
                    customer=CustomerInput(name='Alice Brown', email='alice-brown@email.com', phone='+12345678901'),
                    team_id=teams['Support'].id,
                    market_id=markets['PH'].id,
                    country_code='PH',
                    ai_summary='Customer reports that the parcel has not arrived and asks for an urgent update.',
                    ai_classification='Delivery Delay',
                    ai_confidence=0.86,
                    case_type='Delivery Delay',
                    issue_summary='Customer asks for delivery update',
                    customer_request='Where is my package? It has been over a week already.',
                    source_chat_id='wa-demo-chat-001',
                    required_action='Check shipment status and update customer',
                    missing_fields='',
                    last_customer_message='Where is my package? It has been over a week already.',
                    customer_update='We are checking the shipment and will update you shortly.',
                    resolution_summary='',
                    last_human_update='Case created for manual follow-up',
                    preferred_reply_channel='whatsapp',
                    preferred_reply_contact='+12345678901',
                ),
                creator,
            )
            db.commit()
        if not db.query(AIConfigResource).count():
            creator = db.query(User).filter(User.username == 'admin').first() or db.query(User).filter(User.username == 'ops').first()
            resources = [
                AIConfigResource(
                    resource_key='customer-service-persona',
                    config_type='persona',
                    name='客服品牌语气',
                    description='统一客服语气、边界和升级原则。',
                    scope_type='global',
                    draft_summary='统一礼貌、直接、以解决问题为主的客服语气。',
                    draft_content_json={
                        'tone': 'professional_warm',
                        'brand_voice': ['礼貌', '直接', '不夸张承诺'],
                        'escalation_rule': '涉及赔付、退款、清关争议时主动转人工审批',
                    },
                    created_by=creator.id if creator else None,
                    updated_by=creator.id if creator else None,
                ),
                AIConfigResource(
                    resource_key='operator-knowledge-core',
                    config_type='knowledge',
                    name='客服知识核心包',
                    description='把公告、常见问题和时效口径沉淀为统一知识。',
                    scope_type='global',
                    draft_summary='客服统一引用的知识核心包。',
                    draft_content_json={
                        'sources': ['公告', 'FAQ', '市场规则'],
                        'priority': ['市场公告优先', '工单证据优先', '不要编造未确认信息'],
                    },
                    created_by=creator.id if creator else None,
                    updated_by=creator.id if creator else None,
                ),
                AIConfigResource(
                    resource_key='delivery-delay-sop',
                    config_type='sop',
                    name='延误工单处理SOP',
                    description='用于延误类工单的标准动作。',
                    scope_type='case_type',
                    scope_value='Delivery Delay',
                    market_id=markets['PH'].id,
                    draft_summary='延误工单先核对轨迹，再统一回复，再决定是否升级。',
                    draft_content_json={
                        'steps': ['核对轨迹', '核对公告口径', '给客户一次清晰回复', '超过阈值则升级主管'],
                        'close_rule': '客户确认收到并且问题解决后才允许关单',
                    },
                    created_by=creator.id if creator else None,
                    updated_by=creator.id if creator else None,
                ),
                AIConfigResource(
                    resource_key='customer-reply-policy',
                    config_type='policy',
                    name='智能回复执行边界',
                    description='约束智能助手哪些动作能自动做，哪些必须审批。',
                    scope_type='global',
                    draft_summary='默认只允许建议，不允许高风险承诺自动执行。',
                    draft_content_json={
                        'allow_auto_reply': True,
                        'forbid_claims': ['已退款', '已赔付', '已重新发货'],
                        'approval_required': ['退款', '赔付', '改地址', '清关争议处理'],
                    },
                    created_by=creator.id if creator else None,
                    updated_by=creator.id if creator else None,
                ),
            ]
            db.add_all(resources)
            db.flush()
            for resource in resources:
                publish_resource(db, resource, creator, notes='seed default published config')
            db.commit()

        if not db.query(MarketBulletin).count():
            creator = db.query(User).filter(User.username == 'admin').first() or db.query(User).filter(User.username == 'lead').first()
            db.add(MarketBulletin(
                market_id=markets['PH'].id,
                country_code='PH',
                title='末端派送时效提醒',
                summary='当前部分区域派送时效略有波动，请先按统一口径向客户解释。',
                body='当前部分区域派送时效略有波动，请先核对运单状态，再按统一口径向客户解释，如需升级请转交主管。',
                category='delay',
                audience='operator',
                severity='warning',
                auto_inject_to_ai=True,
                is_active=True,
                created_by=creator.id if creator else None,
            ))
            db.commit()
    finally:
        db.close()


if __name__ == '__main__':
    Base.metadata.create_all(bind=engine)
    seed_data()
    print('Development database initialized.')
