"""add governed operator configuration

Revision ID: 20260721_0073
Revises: 20260721_0072
Create Date: 2026-07-21
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = "20260721_0073"
down_revision = "20260721_0072"
branch_labels = None
depends_on = None


def _as_datetime(value, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return fallback
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return fallback


_COUNTRIES = [('AF', 'AFG', '004', 'Afghanistan', None, None),
 ('AL', 'ALB', '008', 'Albania', '+355', 'ALL'),
 ('DZ', 'DZA', '012', 'Algeria', None, None),
 ('AS', 'ASM', '016', 'American Samoa', None, None),
 ('AD', 'AND', '020', 'Andorra', None, None),
 ('AO', 'AGO', '024', 'Angola', None, None),
 ('AI', 'AIA', '660', 'Anguilla', None, None),
 ('AQ', 'ATA', '010', 'Antarctica', None, None),
 ('AG', 'ATG', '028', 'Antigua and Barbuda', None, None),
 ('AR', 'ARG', '032', 'Argentina', None, None),
 ('AM', 'ARM', '051', 'Armenia', None, None),
 ('AW', 'ABW', '533', 'Aruba', None, None),
 ('AU', 'AUS', '036', 'Australia', None, None),
 ('AT', 'AUT', '040', 'Austria', '+43', 'EUR'),
 ('AZ', 'AZE', '031', 'Azerbaijan', None, None),
 ('BS', 'BHS', '044', 'Bahamas', None, None),
 ('BH', 'BHR', '048', 'Bahrain', None, None),
 ('BD', 'BGD', '050', 'Bangladesh', None, None),
 ('BB', 'BRB', '052', 'Barbados', None, None),
 ('BY', 'BLR', '112', 'Belarus', None, None),
 ('BE', 'BEL', '056', 'Belgium', None, None),
 ('BZ', 'BLZ', '084', 'Belize', None, None),
 ('BJ', 'BEN', '204', 'Benin', None, None),
 ('BM', 'BMU', '060', 'Bermuda', None, None),
 ('BT', 'BTN', '064', 'Bhutan', None, None),
 ('BO', 'BOL', '068', 'Bolivia, Plurinational State of', None, None),
 ('BQ', 'BES', '535', 'Bonaire, Sint Eustatius and Saba', None, None),
 ('BA', 'BIH', '070', 'Bosnia and Herzegovina', '+387', 'BAM'),
 ('BW', 'BWA', '072', 'Botswana', None, None),
 ('BV', 'BVT', '074', 'Bouvet Island', None, None),
 ('BR', 'BRA', '076', 'Brazil', None, None),
 ('IO', 'IOT', '086', 'British Indian Ocean Territory', None, None),
 ('BN', 'BRN', '096', 'Brunei Darussalam', None, None),
 ('BG', 'BGR', '100', 'Bulgaria', '+359', 'BGN'),
 ('BF', 'BFA', '854', 'Burkina Faso', None, None),
 ('BI', 'BDI', '108', 'Burundi', None, None),
 ('CV', 'CPV', '132', 'Cabo Verde', None, None),
 ('KH', 'KHM', '116', 'Cambodia', None, None),
 ('CM', 'CMR', '120', 'Cameroon', None, None),
 ('CA', 'CAN', '124', 'Canada', None, None),
 ('KY', 'CYM', '136', 'Cayman Islands', None, None),
 ('CF', 'CAF', '140', 'Central African Republic', None, None),
 ('TD', 'TCD', '148', 'Chad', None, None),
 ('CL', 'CHL', '152', 'Chile', None, None),
 ('CN', 'CHN', '156', 'China', '+86', 'CNY'),
 ('CX', 'CXR', '162', 'Christmas Island', None, None),
 ('CC', 'CCK', '166', 'Cocos (Keeling) Islands', None, None),
 ('CO', 'COL', '170', 'Colombia', None, None),
 ('KM', 'COM', '174', 'Comoros', None, None),
 ('CG', 'COG', '178', 'Congo', None, None),
 ('CD', 'COD', '180', 'Congo, The Democratic Republic of the', None, None),
 ('CK', 'COK', '184', 'Cook Islands', None, None),
 ('CR', 'CRI', '188', 'Costa Rica', None, None),
 ('HR', 'HRV', '191', 'Croatia', '+385', 'EUR'),
 ('CU', 'CUB', '192', 'Cuba', None, None),
 ('CW', 'CUW', '531', 'Curaçao', None, None),
 ('CY', 'CYP', '196', 'Cyprus', None, None),
 ('CZ', 'CZE', '203', 'Czechia', None, None),
 ('CI', 'CIV', '384', "Côte d'Ivoire", None, None),
 ('DK', 'DNK', '208', 'Denmark', None, None),
 ('DJ', 'DJI', '262', 'Djibouti', None, None),
 ('DM', 'DMA', '212', 'Dominica', None, None),
 ('DO', 'DOM', '214', 'Dominican Republic', None, None),
 ('EC', 'ECU', '218', 'Ecuador', None, None),
 ('EG', 'EGY', '818', 'Egypt', None, None),
 ('SV', 'SLV', '222', 'El Salvador', None, None),
 ('GQ', 'GNQ', '226', 'Equatorial Guinea', None, None),
 ('ER', 'ERI', '232', 'Eritrea', None, None),
 ('EE', 'EST', '233', 'Estonia', None, None),
 ('SZ', 'SWZ', '748', 'Eswatini', None, None),
 ('ET', 'ETH', '231', 'Ethiopia', None, None),
 ('FK', 'FLK', '238', 'Falkland Islands (Malvinas)', None, None),
 ('FO', 'FRO', '234', 'Faroe Islands', None, None),
 ('FJ', 'FJI', '242', 'Fiji', None, None),
 ('FI', 'FIN', '246', 'Finland', None, None),
 ('FR', 'FRA', '250', 'France', '+33', 'EUR'),
 ('GF', 'GUF', '254', 'French Guiana', None, None),
 ('PF', 'PYF', '258', 'French Polynesia', None, None),
 ('TF', 'ATF', '260', 'French Southern Territories', None, None),
 ('GA', 'GAB', '266', 'Gabon', None, None),
 ('GM', 'GMB', '270', 'Gambia', None, None),
 ('GE', 'GEO', '268', 'Georgia', None, None),
 ('DE', 'DEU', '276', 'Germany', '+49', 'EUR'),
 ('GH', 'GHA', '288', 'Ghana', None, None),
 ('GI', 'GIB', '292', 'Gibraltar', None, None),
 ('GR', 'GRC', '300', 'Greece', '+30', 'EUR'),
 ('GL', 'GRL', '304', 'Greenland', None, None),
 ('GD', 'GRD', '308', 'Grenada', None, None),
 ('GP', 'GLP', '312', 'Guadeloupe', None, None),
 ('GU', 'GUM', '316', 'Guam', None, None),
 ('GT', 'GTM', '320', 'Guatemala', None, None),
 ('GG', 'GGY', '831', 'Guernsey', None, None),
 ('GN', 'GIN', '324', 'Guinea', None, None),
 ('GW', 'GNB', '624', 'Guinea-Bissau', None, None),
 ('GY', 'GUY', '328', 'Guyana', None, None),
 ('HT', 'HTI', '332', 'Haiti', None, None),
 ('HM', 'HMD', '334', 'Heard Island and McDonald Islands', None, None),
 ('VA', 'VAT', '336', 'Holy See (Vatican City State)', None, None),
 ('HN', 'HND', '340', 'Honduras', None, None),
 ('HK', 'HKG', '344', 'Hong Kong', None, None),
 ('HU', 'HUN', '348', 'Hungary', None, None),
 ('IS', 'ISL', '352', 'Iceland', None, None),
 ('IN', 'IND', '356', 'India', None, None),
 ('ID', 'IDN', '360', 'Indonesia', None, None),
 ('IR', 'IRN', '364', 'Iran, Islamic Republic of', None, None),
 ('IQ', 'IRQ', '368', 'Iraq', None, None),
 ('IE', 'IRL', '372', 'Ireland', None, None),
 ('IM', 'IMN', '833', 'Isle of Man', None, None),
 ('IL', 'ISR', '376', 'Israel', None, None),
 ('IT', 'ITA', '380', 'Italy', '+39', 'EUR'),
 ('JM', 'JAM', '388', 'Jamaica', None, None),
 ('JP', 'JPN', '392', 'Japan', None, None),
 ('JE', 'JEY', '832', 'Jersey', None, None),
 ('JO', 'JOR', '400', 'Jordan', None, None),
 ('KZ', 'KAZ', '398', 'Kazakhstan', None, None),
 ('KE', 'KEN', '404', 'Kenya', None, None),
 ('KI', 'KIR', '296', 'Kiribati', None, None),
 ('KP', 'PRK', '408', "Korea, Democratic People's Republic of", None, None),
 ('KR', 'KOR', '410', 'Korea, Republic of', None, None),
 ('KW', 'KWT', '414', 'Kuwait', None, None),
 ('KG', 'KGZ', '417', 'Kyrgyzstan', None, None),
 ('LA', 'LAO', '418', "Lao People's Democratic Republic", None, None),
 ('LV', 'LVA', '428', 'Latvia', None, None),
 ('LB', 'LBN', '422', 'Lebanon', None, None),
 ('LS', 'LSO', '426', 'Lesotho', None, None),
 ('LR', 'LBR', '430', 'Liberia', None, None),
 ('LY', 'LBY', '434', 'Libya', None, None),
 ('LI', 'LIE', '438', 'Liechtenstein', None, None),
 ('LT', 'LTU', '440', 'Lithuania', None, None),
 ('LU', 'LUX', '442', 'Luxembourg', None, None),
 ('MO', 'MAC', '446', 'Macao', None, None),
 ('MG', 'MDG', '450', 'Madagascar', None, None),
 ('MW', 'MWI', '454', 'Malawi', None, None),
 ('MY', 'MYS', '458', 'Malaysia', None, None),
 ('MV', 'MDV', '462', 'Maldives', None, None),
 ('ML', 'MLI', '466', 'Mali', None, None),
 ('MT', 'MLT', '470', 'Malta', None, None),
 ('MH', 'MHL', '584', 'Marshall Islands', None, None),
 ('MQ', 'MTQ', '474', 'Martinique', None, None),
 ('MR', 'MRT', '478', 'Mauritania', None, None),
 ('MU', 'MUS', '480', 'Mauritius', None, None),
 ('YT', 'MYT', '175', 'Mayotte', None, None),
 ('MX', 'MEX', '484', 'Mexico', None, None),
 ('FM', 'FSM', '583', 'Micronesia, Federated States of', None, None),
 ('MD', 'MDA', '498', 'Moldova, Republic of', None, None),
 ('MC', 'MCO', '492', 'Monaco', None, None),
 ('MN', 'MNG', '496', 'Mongolia', None, None),
 ('ME', 'MNE', '499', 'Montenegro', '+382', 'EUR'),
 ('MS', 'MSR', '500', 'Montserrat', None, None),
 ('MA', 'MAR', '504', 'Morocco', None, None),
 ('MZ', 'MOZ', '508', 'Mozambique', None, None),
 ('MM', 'MMR', '104', 'Myanmar', None, None),
 ('NA', 'NAM', '516', 'Namibia', None, None),
 ('NR', 'NRU', '520', 'Nauru', None, None),
 ('NP', 'NPL', '524', 'Nepal', None, None),
 ('NL', 'NLD', '528', 'Netherlands', None, None),
 ('NC', 'NCL', '540', 'New Caledonia', None, None),
 ('NZ', 'NZL', '554', 'New Zealand', None, None),
 ('NI', 'NIC', '558', 'Nicaragua', None, None),
 ('NE', 'NER', '562', 'Niger', None, None),
 ('NG', 'NGA', '566', 'Nigeria', None, None),
 ('NU', 'NIU', '570', 'Niue', None, None),
 ('NF', 'NFK', '574', 'Norfolk Island', None, None),
 ('MK', 'MKD', '807', 'North Macedonia', '+389', 'MKD'),
 ('MP', 'MNP', '580', 'Northern Mariana Islands', None, None),
 ('NO', 'NOR', '578', 'Norway', None, None),
 ('OM', 'OMN', '512', 'Oman', None, None),
 ('PK', 'PAK', '586', 'Pakistan', None, None),
 ('PW', 'PLW', '585', 'Palau', None, None),
 ('PS', 'PSE', '275', 'Palestine, State of', None, None),
 ('PA', 'PAN', '591', 'Panama', None, None),
 ('PG', 'PNG', '598', 'Papua New Guinea', None, None),
 ('PY', 'PRY', '600', 'Paraguay', None, None),
 ('PE', 'PER', '604', 'Peru', None, None),
 ('PH', 'PHL', '608', 'Philippines', None, None),
 ('PN', 'PCN', '612', 'Pitcairn', None, None),
 ('PL', 'POL', '616', 'Poland', None, None),
 ('PT', 'PRT', '620', 'Portugal', None, None),
 ('PR', 'PRI', '630', 'Puerto Rico', None, None),
 ('QA', 'QAT', '634', 'Qatar', None, None),
 ('RO', 'ROU', '642', 'Romania', '+40', 'RON'),
 ('RU', 'RUS', '643', 'Russian Federation', None, None),
 ('RW', 'RWA', '646', 'Rwanda', None, None),
 ('RE', 'REU', '638', 'Réunion', None, None),
 ('BL', 'BLM', '652', 'Saint Barthélemy', None, None),
 ('SH', 'SHN', '654', 'Saint Helena, Ascension and Tristan da Cunha', None, None),
 ('KN', 'KNA', '659', 'Saint Kitts and Nevis', None, None),
 ('LC', 'LCA', '662', 'Saint Lucia', None, None),
 ('MF', 'MAF', '663', 'Saint Martin (French part)', None, None),
 ('PM', 'SPM', '666', 'Saint Pierre and Miquelon', None, None),
 ('VC', 'VCT', '670', 'Saint Vincent and the Grenadines', None, None),
 ('WS', 'WSM', '882', 'Samoa', None, None),
 ('SM', 'SMR', '674', 'San Marino', None, None),
 ('ST', 'STP', '678', 'Sao Tome and Principe', None, None),
 ('SA', 'SAU', '682', 'Saudi Arabia', None, None),
 ('SN', 'SEN', '686', 'Senegal', None, None),
 ('RS', 'SRB', '688', 'Serbia', '+381', 'RSD'),
 ('SC', 'SYC', '690', 'Seychelles', None, None),
 ('SL', 'SLE', '694', 'Sierra Leone', None, None),
 ('SG', 'SGP', '702', 'Singapore', None, None),
 ('SX', 'SXM', '534', 'Sint Maarten (Dutch part)', None, None),
 ('SK', 'SVK', '703', 'Slovakia', None, None),
 ('SI', 'SVN', '705', 'Slovenia', '+386', 'EUR'),
 ('SB', 'SLB', '090', 'Solomon Islands', None, None),
 ('SO', 'SOM', '706', 'Somalia', None, None),
 ('ZA', 'ZAF', '710', 'South Africa', None, None),
 ('GS', 'SGS', '239', 'South Georgia and the South Sandwich Islands', None, None),
 ('SS', 'SSD', '728', 'South Sudan', None, None),
 ('ES', 'ESP', '724', 'Spain', None, None),
 ('LK', 'LKA', '144', 'Sri Lanka', None, None),
 ('SD', 'SDN', '729', 'Sudan', None, None),
 ('SR', 'SUR', '740', 'Suriname', None, None),
 ('SJ', 'SJM', '744', 'Svalbard and Jan Mayen', None, None),
 ('SE', 'SWE', '752', 'Sweden', None, None),
 ('CH', 'CHE', '756', 'Switzerland', '+41', 'CHF'),
 ('SY', 'SYR', '760', 'Syrian Arab Republic', None, None),
 ('TW', 'TWN', '158', 'Taiwan, Province of China', None, None),
 ('TJ', 'TJK', '762', 'Tajikistan', None, None),
 ('TZ', 'TZA', '834', 'Tanzania, United Republic of', None, None),
 ('TH', 'THA', '764', 'Thailand', None, None),
 ('TL', 'TLS', '626', 'Timor-Leste', None, None),
 ('TG', 'TGO', '768', 'Togo', None, None),
 ('TK', 'TKL', '772', 'Tokelau', None, None),
 ('TO', 'TON', '776', 'Tonga', None, None),
 ('TT', 'TTO', '780', 'Trinidad and Tobago', None, None),
 ('TN', 'TUN', '788', 'Tunisia', None, None),
 ('TM', 'TKM', '795', 'Turkmenistan', None, None),
 ('TC', 'TCA', '796', 'Turks and Caicos Islands', None, None),
 ('TV', 'TUV', '798', 'Tuvalu', None, None),
 ('TR', 'TUR', '792', 'Türkiye', None, None),
 ('UG', 'UGA', '800', 'Uganda', None, None),
 ('UA', 'UKR', '804', 'Ukraine', '+380', 'UAH'),
 ('AE', 'ARE', '784', 'United Arab Emirates', None, None),
 ('GB', 'GBR', '826', 'United Kingdom', '+44', 'GBP'),
 ('US', 'USA', '840', 'United States', '+1', 'USD'),
 ('UM', 'UMI', '581', 'United States Minor Outlying Islands', None, None),
 ('UY', 'URY', '858', 'Uruguay', None, None),
 ('UZ', 'UZB', '860', 'Uzbekistan', None, None),
 ('VU', 'VUT', '548', 'Vanuatu', None, None),
 ('VE', 'VEN', '862', 'Venezuela, Bolivarian Republic of', None, None),
 ('VN', 'VNM', '704', 'Viet Nam', None, None),
 ('VG', 'VGB', '092', 'Virgin Islands, British', None, None),
 ('VI', 'VIR', '850', 'Virgin Islands, U.S.', None, None),
 ('WF', 'WLF', '876', 'Wallis and Futuna', None, None),
 ('EH', 'ESH', '732', 'Western Sahara', None, None),
 ('YE', 'YEM', '887', 'Yemen', None, None),
 ('ZM', 'ZMB', '894', 'Zambia', None, None),
 ('ZW', 'ZWE', '716', 'Zimbabwe', None, None),
 ('AX', 'ALA', '248', 'Åland Islands', None, None)]

_SYSTEM_ROLE_TEMPLATES = (
    (
        "system.admin",
        "系统管理员",
        "admin",
        "administrator",
        [
            "ai_config.manage", "ai_config.read", "ai_intake.write", "attachment.read.external",
            "attachment.read.internal", "attachment.upload", "audit.read", "bulletin.manage",
            "channel_account.manage", "customer_profile.read", "market.manage", "note.write.external",
            "note.write.internal", "operator_queue.read", "outbound.draft.save", "outbound.send",
            "qa.manage", "runtime.manage", "security.read", "ticket.assign", "ticket.close",
            "ticket.escalate", "ticket.read", "ticket.status.change", "ticket.update_core",
            "tool:speedaf.order.cancel:write", "tool:speedaf.order.update_address:write",
            "tool:speedaf.voice.callback:write", "tool:speedaf.work_order.create:write",
            "user.manage", "webcall.voice.accept", "webcall.voice.control", "webcall.voice.end",
            "webcall.voice.queue.view", "webcall.voice.read", "webcall.voice.reject",
            "webchat.conversation.monitor_ai", "webchat.handoff.accept", "webchat.handoff.decline",
            "webchat.handoff.force_takeover", "webchat.handoff.release", "webchat.handoff.resume_ai",
        ],
    ),
    (
        "system.manager",
        "运营经理",
        "manager",
        "sensitive",
        [
            "ai_intake.write", "attachment.read.external", "attachment.read.internal",
            "attachment.upload", "bulletin.manage", "customer_profile.read", "note.write.external",
            "note.write.internal", "operator_queue.read", "outbound.draft.save", "outbound.send",
            "qa.manage", "ticket.assign", "ticket.close", "ticket.escalate", "ticket.read",
            "ticket.status.change", "ticket.update_core", "webchat.conversation.monitor_ai",
            "webchat.handoff.accept", "webchat.handoff.decline", "webchat.handoff.force_takeover",
            "webchat.handoff.release", "webchat.handoff.resume_ai",
        ],
    ),
    (
        "system.lead",
        "客服组长",
        "lead",
        "sensitive",
        [
            "ai_intake.write", "attachment.read.external", "attachment.read.internal",
            "attachment.upload", "customer_profile.read", "note.write.external",
            "note.write.internal", "operator_queue.read", "outbound.draft.save", "outbound.send",
            "qa.manage", "ticket.assign", "ticket.close", "ticket.escalate", "ticket.read",
            "ticket.status.change", "ticket.update_core", "webchat.conversation.monitor_ai",
            "webchat.handoff.accept", "webchat.handoff.decline", "webchat.handoff.force_takeover",
            "webchat.handoff.release", "webchat.handoff.resume_ai",
        ],
    ),
    (
        "system.agent",
        "客服专员",
        "agent",
        "standard",
        [
            "ai_intake.write", "attachment.read.external", "attachment.read.internal",
            "attachment.upload", "customer_profile.read", "note.write.external",
            "note.write.internal", "operator_queue.read", "outbound.draft.save", "outbound.send",
            "ticket.read", "ticket.status.change", "webchat.conversation.monitor_ai",
            "webchat.handoff.accept", "webchat.handoff.decline", "webchat.handoff.release",
        ],
    ),
    (
        "system.auditor",
        "审计员",
        "auditor",
        "standard",
        [
            "attachment.read.external", "attachment.read.internal", "audit.read",
            "customer_profile.read", "operator_queue.read", "security.read", "ticket.read",
        ],
    ),
)


def upgrade() -> None:
    now = datetime.now(timezone.utc)
    op.create_table(
        "country_catalog",
        sa.Column("iso_alpha2", sa.String(length=2), primary_key=True),
        sa.Column("iso_alpha3", sa.String(length=3), nullable=False),
        sa.Column("iso_numeric", sa.String(length=3), nullable=True),
        sa.Column("canonical_name", sa.String(length=160), nullable=False),
        sa.Column("calling_code", sa.String(length=32), nullable=True),
        sa.Column("default_currency", sa.String(length=3), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("iso_alpha3", name="uq_country_catalog_alpha3"),
        sa.UniqueConstraint("iso_numeric", name="uq_country_catalog_numeric"),
    )
    op.create_index("ix_country_catalog_alpha3", "country_catalog", ["iso_alpha3"], unique=True)
    op.create_index("ix_country_catalog_name", "country_catalog", ["canonical_name"])
    op.create_index("ix_country_catalog_currency", "country_catalog", ["default_currency"])
    op.create_index("ix_country_catalog_available", "country_catalog", ["is_available"])

    country_table = sa.table(
        "country_catalog",
        sa.column("iso_alpha2", sa.String()),
        sa.column("iso_alpha3", sa.String()),
        sa.column("iso_numeric", sa.String()),
        sa.column("canonical_name", sa.String()),
        sa.column("calling_code", sa.String()),
        sa.column("default_currency", sa.String()),
        sa.column("is_available", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        country_table,
        [
            {
                "iso_alpha2": alpha2,
                "iso_alpha3": alpha3,
                "iso_numeric": numeric,
                "canonical_name": name,
                "calling_code": calling,
                "default_currency": currency,
                "is_available": True,
                "created_at": now,
                "updated_at": now,
            }
            for alpha2, alpha3, numeric, name, calling, currency in _COUNTRIES
        ],
    )

    op.create_table(
        "market_governance_profiles",
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("default_currency", sa.String(length=3), nullable=True),
        sa.Column("owner_team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("data_region", sa.String(length=80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retired_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'retiring', 'retired')",
            name="ck_market_governance_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_market_governance_version_positive"),
    )
    op.create_index("ix_market_governance_status", "market_governance_profiles", ["status"])
    op.create_index("ix_market_governance_owner_team_id", "market_governance_profiles", ["owner_team_id"])
    op.create_index("ix_market_governance_retired_by", "market_governance_profiles", ["retired_by"])
    op.create_index("ix_market_governance_retired_at", "market_governance_profiles", ["retired_at"])
    op.create_index("ix_market_governance_created_by", "market_governance_profiles", ["created_by"])
    op.create_index("ix_market_governance_updated_by", "market_governance_profiles", ["updated_by"])

    op.create_table(
        "market_countries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("country_code", sa.String(length=2), sa.ForeignKey("country_catalog.iso_alpha2", ondelete="RESTRICT"), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("market_id", "country_code", name="uq_market_country"),
    )
    op.create_index("ix_market_countries_market_id", "market_countries", ["market_id"])
    op.create_index("ix_market_countries_country_code", "market_countries", ["country_code"])
    op.create_index("ix_market_country_primary", "market_countries", ["market_id", "is_primary"])

    op.create_table(
        "market_languages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language_code", sa.String(length=24), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("market_id", "language_code", name="uq_market_language"),
    )
    op.create_index("ix_market_languages_market_id", "market_languages", ["market_id"])
    op.create_index("ix_market_languages_language_code", "market_languages", ["language_code"])
    op.create_index("ix_market_language_primary", "market_languages", ["market_id", "is_primary"])

    op.create_table(
        "role_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("role_key", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("base_role", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=24), nullable=False, server_default="standard"),
        sa.Column("is_system_protected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("draft_capabilities_json", sa.JSON(), nullable=False),
        sa.Column("published_capabilities_json", sa.JSON(), nullable=True),
        sa.Column("published_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "base_role IN ('admin', 'manager', 'lead', 'agent', 'auditor')",
            name="ck_role_template_base_role",
        ),
        sa.CheckConstraint(
            "risk_level IN ('standard', 'sensitive', 'administrator')",
            name="ck_role_template_risk",
        ),
        sa.CheckConstraint(
            "published_version >= 0", name="ck_role_template_version_nonnegative"
        ),
    )
    op.create_index(
        "uq_role_template_global_key",
        "role_templates",
        ["role_key"],
        unique=True,
        sqlite_where=sa.text("tenant_id IS NULL"),
        postgresql_where=sa.text("tenant_id IS NULL"),
    )
    op.create_index(
        "uq_role_template_tenant_key",
        "role_templates",
        ["tenant_id", "role_key"],
        unique=True,
        sqlite_where=sa.text("tenant_id IS NOT NULL"),
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )

    for name, columns in (
        ("ix_role_templates_tenant_id", ["tenant_id"]),
        ("ix_role_templates_role_key", ["role_key"]),
        ("ix_role_templates_display_name", ["display_name"]),
        ("ix_role_templates_risk_level", ["risk_level"]),
        ("ix_role_templates_is_active", ["is_active"]),
        ("ix_role_templates_published_at", ["published_at"]),
        ("ix_role_templates_created_by", ["created_by"]),
        ("ix_role_templates_updated_by", ["updated_by"]),
        ("ix_role_templates_published_by", ["published_by"]),
        ("ix_role_template_tenant_active", ["tenant_id", "is_active"]),
    ):
        op.create_index(name, "role_templates", columns)

    role_table = sa.table(
        "role_templates",
        sa.column("tenant_id", sa.Integer()),
        sa.column("role_key", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("base_role", sa.String()),
        sa.column("risk_level", sa.String()),
        sa.column("is_system_protected", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
        sa.column("draft_capabilities_json", sa.JSON()),
        sa.column("published_capabilities_json", sa.JSON()),
        sa.column("published_version", sa.Integer()),
        sa.column("published_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        role_table,
        [
            {
                "tenant_id": None,
                "role_key": key,
                "display_name": display_name,
                "description": "系统内置角色模板",
                "base_role": base_role,
                "risk_level": risk,
                "is_system_protected": True,
                "is_active": True,
                "draft_capabilities_json": capabilities,
                "published_capabilities_json": capabilities,
                "published_version": 1,
                "published_at": now,
                "created_at": now,
                "updated_at": now,
            }
            for key, display_name, base_role, risk, capabilities in _SYSTEM_ROLE_TEMPLATES
        ],
    )

    op.create_table(
        "role_template_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("role_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("template_id", "version", name="uq_role_template_version"),
    )
    op.create_index("ix_role_template_versions_template_id", "role_template_versions", ["template_id"])
    op.create_index("ix_role_template_versions_version", "role_template_versions", ["version"])
    op.create_index("ix_role_template_versions_published_by", "role_template_versions", ["published_by"])
    op.create_index("ix_role_template_versions_published_at", "role_template_versions", ["published_at"])

    bind = op.get_bind()
    templates = bind.execute(
        sa.text("SELECT id, role_key, display_name, description, base_role, risk_level, published_capabilities_json FROM role_templates")
    ).mappings().all()
    version_table = sa.table(
        "role_template_versions",
        sa.column("template_id", sa.Integer()),
        sa.column("version", sa.Integer()),
        sa.column("snapshot_json", sa.JSON()),
        sa.column("notes", sa.Text()),
        sa.column("published_by", sa.Integer()),
        sa.column("published_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        version_table,
        [
            {
                "template_id": row["id"],
                "version": 1,
                "snapshot_json": {
                    "role_key": row["role_key"],
                    "display_name": row["display_name"],
                    "description": row["description"],
                    "base_role": row["base_role"],
                    "risk_level": row["risk_level"],
                    "capabilities": row["published_capabilities_json"],
                    "version": 1,
                    "published_at": now.isoformat(),
                },
                "notes": "Seeded from canonical role policy",
                "published_by": None,
                "published_at": now,
            }
            for row in templates
        ],
    )

    op.create_table(
        "role_template_assignments",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("role_templates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("assigned_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "template_version > 0",
            name="ck_role_template_assignment_version_positive",
        ),
    )
    op.create_index("ix_role_template_assignments_template_id", "role_template_assignments", ["template_id"])
    op.create_index("ix_role_template_assignments_assigned_by", "role_template_assignments", ["assigned_by"])
    op.create_index("ix_role_template_assignments_assigned_at", "role_template_assignments", ["assigned_at"])

    op.create_table(
        "knowledge_import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="processing"),
        sa.Column("total_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=True),
        sa.Column("audience_scope", sa.String(length=40), nullable=False, server_default="customer"),
        sa.Column("language", sa.String(length=24), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('processing', 'ready', 'partial', 'failed', 'cancelled')",
            name="ck_knowledge_import_batch_status",
        ),
        sa.CheckConstraint(
            "total_files >= 0 AND succeeded_files >= 0 AND failed_files >= 0 "
            "AND duplicate_files >= 0",
            name="ck_knowledge_import_batch_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "succeeded_files + failed_files + duplicate_files <= total_files",
            name="ck_knowledge_import_batch_counts_bounded",
        ),
    )
    for name, columns in (
        ("ix_knowledge_import_batches_tenant_id", ["tenant_id"]),
        ("ix_knowledge_import_batches_status", ["status"]),
        ("ix_knowledge_import_batches_market_id", ["market_id"]),
        ("ix_knowledge_import_batches_created_by", ["created_by"]),
        ("ix_knowledge_import_batches_created_at", ["created_at"]),
        ("ix_knowledge_import_batches_completed_at", ["completed_at"]),
        ("ix_knowledge_import_batch_tenant_created", ["tenant_id", "created_at"]),
    ):
        op.create_index(name, "knowledge_import_batches", columns)

    op.create_table(
        "knowledge_import_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("knowledge_import_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.String(length=80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("original_file_name", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("knowledge_item_id", sa.Integer(), sa.ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("duplicate_of_document_id", sa.Integer(), sa.ForeignKey("knowledge_import_documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("batch_id", "position", name="uq_knowledge_import_position"),
        sa.CheckConstraint(
            "position > 0", name="ck_knowledge_import_document_position_positive"
        ),
        sa.CheckConstraint(
            "status IN ('draft_created', 'duplicate', 'failed')",
            name="ck_knowledge_import_document_status",
        ),
    )
    for name, columns in (
        ("ix_knowledge_import_documents_batch_id", ["batch_id"]),
        ("ix_knowledge_import_documents_tenant_id", ["tenant_id"]),
        ("ix_knowledge_import_documents_sha256", ["sha256"]),
        ("ix_knowledge_import_documents_status", ["status"]),
        ("ix_knowledge_import_documents_knowledge_item_id", ["knowledge_item_id"]),
        ("ix_knowledge_import_documents_duplicate_of_document_id", ["duplicate_of_document_id"]),
        ("ix_knowledge_import_documents_created_at", ["created_at"]),
        ("ix_knowledge_import_document_hash", ["tenant_id", "sha256"]),
    ):
        op.create_index(name, "knowledge_import_documents", columns)

    op.create_table(
        "agent_deployment_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deployment_id", sa.Integer(), sa.ForeignKey("agent_deployments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=False),
        sa.Column("after_json", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("deployment_id", "revision", name="uq_agent_deployment_revision"),
        sa.CheckConstraint(
            "action IN ('deploy', 'canary_start', 'canary_adjust', 'canary_pause', 'canary_promote', 'rollback')",
            name="ck_agent_deployment_revision_action",
        ),
        sa.CheckConstraint(
            "revision > 0", name="ck_agent_deployment_revision_positive"
        ),
    )
    op.create_index("ix_agent_deployment_revisions_deployment_id", "agent_deployment_revisions", ["deployment_id"])
    op.create_index("ix_agent_deployment_revisions_action", "agent_deployment_revisions", ["action"])
    op.create_index("ix_agent_deployment_revisions_created_by", "agent_deployment_revisions", ["created_by"])
    op.create_index("ix_agent_deployment_revisions_created_at", "agent_deployment_revisions", ["created_at"])

    market_rows = bind.execute(
        sa.text("SELECT id, country_code, language_code, is_active, created_at, updated_at FROM markets")
    ).mappings().all()
    profile_table = sa.table(
        "market_governance_profiles",
        sa.column("market_id", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    market_country_table = sa.table(
        "market_countries",
        sa.column("market_id", sa.Integer()),
        sa.column("country_code", sa.String()),
        sa.column("is_primary", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    market_language_table = sa.table(
        "market_languages",
        sa.column("market_id", sa.Integer()),
        sa.column("language_code", sa.String()),
        sa.column("is_primary", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    valid_codes = {row[0] for row in _COUNTRIES}
    if market_rows:
        op.bulk_insert(
            profile_table,
            [
                {
                    "market_id": row["id"],
                    "status": "active" if row["is_active"] else "paused",
                    "version": 1,
                    "created_at": _as_datetime(row["created_at"], now),
                    "updated_at": _as_datetime(row["updated_at"], now),
                }
                for row in market_rows
            ],
        )
        op.bulk_insert(
            market_country_table,
            [
                {
                    "market_id": row["id"],
                    "country_code": str(row["country_code"]).upper(),
                    "is_primary": True,
                    "created_at": now,
                }
                for row in market_rows
                if row["country_code"] and str(row["country_code"]).upper() in valid_codes
            ],
        )
        op.bulk_insert(
            market_language_table,
            [
                {
                    "market_id": row["id"],
                    "language_code": str(row["language_code"]).lower(),
                    "is_primary": True,
                    "created_at": now,
                }
                for row in market_rows
                if row["language_code"]
            ],
        )


def downgrade() -> None:
    op.drop_table("agent_deployment_revisions")
    op.drop_table("knowledge_import_documents")
    op.drop_table("knowledge_import_batches")
    op.drop_table("role_template_assignments")
    op.drop_table("role_template_versions")
    op.drop_table("role_templates")
    op.drop_table("market_languages")
    op.drop_table("market_countries")
    op.drop_table("market_governance_profiles")
    op.drop_table("country_catalog")
