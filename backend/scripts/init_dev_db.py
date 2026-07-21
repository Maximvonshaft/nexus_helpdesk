from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth_service import hash_password
from app.db import Base, SessionLocal, engine
from app.enums import TicketPriority, UserRole
from app.models import Customer, Market, MarketBulletin, Tag, Team, User
from app.schemas import CustomerInput, TicketCreate
from app.services.sla_service import seed_default_sla_policies
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

        # Canonical Agent resources are seeded exclusively by Alembic migrations.
        # Development bootstrap must not recreate retired persona/knowledge/SOP/
        # policy rows or introduce a second configuration authority.
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
