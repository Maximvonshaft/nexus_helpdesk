#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from typing import Any

from response_builder import build_tracking_reply
from speedaf_client import SpeedafLookupError, track_query as speedaf_track_query

HELPDESK_API_BASE = os.getenv('NEXUSDESK_API_URL') or os.getenv('HELPDESK_API_BASE', 'http://127.0.0.1:8888/api').rstrip('/')
HELPDESK_USERNAME = os.getenv('HELPDESK_USERNAME', 'agent')
HELPDESK_PASSWORD = os.getenv('HELPDESK_PASSWORD', 'demo123')
HTTP_TIMEOUT = float(os.getenv('HELPDESK_TIMEOUT_SECONDS', '20'))

CASE_TYPES = {
    'tracking': 'Tracking',
    'reschedule': 'Reschedule',
    'address_change': 'Address Change',
    'shipment_exception': 'Shipment Exception',
    'customs_documents': 'Customs/Documents',
    'complaint': 'Complaint',
    'sales_inquiry': 'Sales Inquiry',
    'other': 'Other',
}
PRIORITIES = {'low': 'Low', 'normal': 'Normal', 'high': 'High', 'urgent': 'Urgent'}
LANGUAGES = {"english": "English", "chinese": "Chinese", "mixed": "Mixed", "macedonian": "Macedonian", "french": "French", "spanish": "Spanish", "arabic": "Arabic", "russian": "Russian", "other": "Other"}
CHANNELS = {'whatsapp': 'WhatsApp', 'telegram': 'Telegram', 'webchat': 'WebChat', 'email': 'Email', 'other': 'Other'}
REPLY_CHANNELS = {'whatsapp': 'WhatsApp', 'email': 'Email', 'phone': 'Phone', 'other': 'Other'}
COUNTRIES = {
    'afghanistan': 'Afghanistan',
    'albania': 'Albania',
    'algeria': 'Algeria',
    'andorra': 'Andorra',
    'angola': 'Angola',
    'antigua_and_barbuda': 'Antigua and Barbuda',
    'argentina': 'Argentina',
    'armenia': 'Armenia',
    'australia': 'Australia',
    'austria': 'Austria',
    'azerbaijan': 'Azerbaijan',
    'bahamas': 'Bahamas',
    'bahrain': 'Bahrain',
    'bangladesh': 'Bangladesh',
    'barbados': 'Barbados',
    'belarus': 'Belarus',
    'belgium': 'Belgium',
    'belize': 'Belize',
    'benin': 'Benin',
    'bhutan': 'Bhutan',
    'bolivia': 'Bolivia',
    'bosnia_and_herzegovina': 'Bosnia and Herzegovina',
    'botswana': 'Botswana',
    'brazil': 'Brazil',
    'brunei_darussalam': 'Brunei Darussalam',
    'bulgaria': 'Bulgaria',
    'burkina_faso': 'Burkina Faso',
    'burundi': 'Burundi',
    'cabo_verde': 'Cabo Verde',
    'cambodia': 'Cambodia',
    'cameroon': 'Cameroon',
    'canada': 'Canada',
    'central_african_republic': 'Central African Republic',
    'chad': 'Chad',
    'chile': 'Chile',
    'china': 'China',
    'colombia': 'Colombia',
    'comoros': 'Comoros',
    'congo': 'Congo',
    'costa_rica': 'Costa Rica',
    'cote_divoire': 'Côte d’Ivoire',
    'croatia': 'Croatia',
    'cuba': 'Cuba',
    'cyprus': 'Cyprus',
    'czech_republic': 'Czech Republic',
    'democratic_peoples_republic_of_korea': 'Democratic People’s Republic of Korea',
    'democratic_republic_of_the_congo': 'Democratic Republic of the Congo',
    'denmark': 'Denmark',
    'djibouti': 'Djibouti',
    'dominica': 'Dominica',
    'dominican_republic': 'Dominican Republic',
    'ecuador': 'Ecuador',
    'egypt': 'Egypt',
    'el_salvador': 'El Salvador',
    'equatorial_guinea': 'Equatorial Guinea',
    'eritrea': 'Eritrea',
    'estonia': 'Estonia',
    'eswatini': 'Eswatini',
    'ethiopia': 'Ethiopia',
    'fiji': 'Fiji',
    'finland': 'Finland',
    'france': 'France',
    'gabon': 'Gabon',
    'gambia': 'Gambia',
    'georgia': 'Georgia',
    'germany': 'Germany',
    'ghana': 'Ghana',
    'greece': 'Greece',
    'grenada': 'Grenada',
    'guatemala': 'Guatemala',
    'guinea': 'Guinea',
    'guinea_bissau': 'Guinea-Bissau',
    'guyana': 'Guyana',
    'haiti': 'Haiti',
    'holy_see': 'Holy See',
    'honduras': 'Honduras',
    'hungary': 'Hungary',
    'iceland': 'Iceland',
    'india': 'India',
    'indonesia': 'Indonesia',
    'iran': 'Iran',
    'iraq': 'Iraq',
    'ireland': 'Ireland',
    'israel': 'Israel',
    'italy': 'Italy',
    'jamaica': 'Jamaica',
    'japan': 'Japan',
    'jordan': 'Jordan',
    'kazakhstan': 'Kazakhstan',
    'kenya': 'Kenya',
    'kiribati': 'Kiribati',
    'kuwait': 'Kuwait',
    'kyrgyzstan': 'Kyrgyzstan',
    'lao_peoples_democratic_republic': 'Lao People’s Democratic Republic',
    'latvia': 'Latvia',
    'lebanon': 'Lebanon',
    'lesotho': 'Lesotho',
    'liberia': 'Liberia',
    'libya': 'Libya',
    'liechtenstein': 'Liechtenstein',
    'lithuania': 'Lithuania',
    'luxembourg': 'Luxembourg',
    'madagascar': 'Madagascar',
    'malawi': 'Malawi',
    'malaysia': 'Malaysia',
    'maldives': 'Maldives',
    'mali': 'Mali',
    'malta': 'Malta',
    'marshall_islands': 'Marshall Islands',
    'mauritania': 'Mauritania',
    'mauritius': 'Mauritius',
    'mexico': 'Mexico',
    'micronesia': 'Federated States of Micronesia',
    'monaco': 'Monaco',
    'mongolia': 'Mongolia',
    'montenegro': 'Montenegro',
    'morocco': 'Morocco',
    'mozambique': 'Mozambique',
    'myanmar': 'Myanmar',
    'namibia': 'Namibia',
    'nauru': 'Nauru',
    'nepal': 'Nepal',
    'netherlands': 'Netherlands',
    'new_zealand': 'New Zealand',
    'nicaragua': 'Nicaragua',
    'niger': 'Niger',
    'nigeria': 'Nigeria',
    'north_macedonia': 'North Macedonia',
    'norway': 'Norway',
    'oman': 'Oman',
    'pakistan': 'Pakistan',
    'palau': 'Palau',
    'panama': 'Panama',
    'papua_new_guinea': 'Papua New Guinea',
    'paraguay': 'Paraguay',
    'peru': 'Peru',
    'philippines': 'Philippines',
    'poland': 'Poland',
    'portugal': 'Portugal',
    'qatar': 'Qatar',
    'republic_of_korea': 'Republic of Korea',
    'republic_of_moldova': 'Republic of Moldova',
    'romania': 'Romania',
    'russian_federation': 'Russian Federation',
    'rwanda': 'Rwanda',
    'saint_kitts_and_nevis': 'Saint Kitts and Nevis',
    'saint_lucia': 'Saint Lucia',
    'saint_vincent_and_the_grenadines': 'Saint Vincent and the Grenadines',
    'samoa': 'Samoa',
    'san_marino': 'San Marino',
    'sao_tome_and_principe': 'Sao Tome and Principe',
    'saudi_arabia': 'Saudi Arabia',
    'senegal': 'Senegal',
    'serbia': 'Serbia',
    'seychelles': 'Seychelles',
    'sierra_leone': 'Sierra Leone',
    'singapore': 'Singapore',
    'slovakia': 'Slovakia',
    'slovenia': 'Slovenia',
    'solomon_islands': 'Solomon Islands',
    'somalia': 'Somalia',
    'south_africa': 'South Africa',
    'south_sudan': 'South Sudan',
    'spain': 'Spain',
    'sri_lanka': 'Sri Lanka',
    'state_of_palestine': 'State of Palestine',
    'sudan': 'Sudan',
    'suriname': 'Suriname',
    'sweden': 'Sweden',
    'switzerland': 'Switzerland',
    'syrian_arab_republic': 'Syrian Arab Republic',
    'tajikistan': 'Tajikistan',
    'thailand': 'Thailand',
    'timor_leste': 'Timor-Leste',
    'togo': 'Togo',
    'tonga': 'Tonga',
    'trinidad_and_tobago': 'Trinidad and Tobago',
    'tunisia': 'Tunisia',
    'turkiye': 'Türkiye',
    'turkmenistan': 'Turkmenistan',
    'tuvalu': 'Tuvalu',
    'uganda': 'Uganda',
    'ukraine': 'Ukraine',
    'united_arab_emirates': 'United Arab Emirates',
    'united_kingdom': 'United Kingdom',
    'united_republic_of_tanzania': 'United Republic of Tanzania',
    'united_states': 'United States',
    'uruguay': 'Uruguay',
    'uzbekistan': 'Uzbekistan',
    'vanuatu': 'Vanuatu',
    'venezuela': 'Venezuela',
    'viet_nam': 'Viet Nam',
    'yemen': 'Yemen',
    'zambia': 'Zambia',
    'zimbabwe': 'Zimbabwe',
}

ALLOWED_ACTIONS = {'submit', 'lookup', 'auto'}
ALLOWED_SOURCES = {'local', 'speedaf'}
MAX_SHORT = 200
MAX_LONG = 1800


def norm_text(value: str, limit: int = MAX_LONG) -> str:
    value = (value or '').replace('\r', ' ').replace('\n', ' ').strip()
    return value[:limit]


def norm_phone(value: str) -> str:
    return (value or '').strip()[:MAX_SHORT]


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() not in {'false', '0', 'no', 'off'}


def language_family(language: str) -> str:
    return 'zh' if language == 'chinese' else 'en'


def error_reply(code: str, language: str) -> dict[str, Any]:
    safe = (
        '当前暂时无法完成该操作，请把运单号、联系方式和诉求再发我一次，我继续帮您处理。'
        if language_family(language) == 'zh'
        else 'I cannot complete that action right now. Please send the tracking number, contact detail, and request again, and I will continue helping.'
    )
    return {'ok': False, 'error': code, 'customer_safe_reply': safe}


def attach_proof(data: dict[str, Any]) -> dict[str, Any]:
    if not data.get('ok') or data.get('dry_run'):
        return data

    action = norm_text(str(data.get('action') or ''), MAX_SHORT).lower()
    case_ref = norm_text(str(data.get('case_ref') or ''), MAX_SHORT)
    status = norm_text(str(data.get('status') or ''), MAX_SHORT)
    found = data.get('found')

    proof_tag = ''
    if action == 'lookup' and found and case_ref:
        proof_tag = f'[[proof:lookup:case_ref={case_ref}:status={status or "unknown"}]]'
    elif action in {'submit', 'auto'} and case_ref:
        proof_tag = f'[[proof:submit:case_ref={case_ref}:status={status or "unknown"}]]'
    elif action == 'tracking_lookup':
        tracking_number = norm_text(str(data.get('tracking_number') or ''), MAX_SHORT)
        if tracking_number:
            proof_tag = f'[[proof:lookup:tracking:{tracking_number}]]'

    if proof_tag:
        data = dict(data)
        data['proof_tag'] = proof_tag
    return data


def json_request(path: str, payload: dict[str, Any], *, token: str | None = None) -> dict[str, Any]:
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(
        f'{HELPDESK_API_BASE}{path}',
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.load(resp)


def login_token() -> str:
    out = json_request('/auth/login', {'username': HELPDESK_USERNAME, 'password': HELPDESK_PASSWORD})
    token = out.get('access_token')
    if not token:
        raise RuntimeError('missing access token')
    return token


def validate_args(args: argparse.Namespace) -> None:
    if args.action not in ALLOWED_ACTIONS:
        raise ValueError('Invalid action')
    if args.source not in ALLOWED_SOURCES:
        raise ValueError('Invalid source')
    if args.priority not in PRIORITIES:
        raise ValueError('Invalid priority')
    if args.issue_type not in CASE_TYPES:
        raise ValueError('Invalid issue type')
    if args.language not in LANGUAGES:
        raise ValueError('Invalid language')
    if args.channel not in CHANNELS:
        raise ValueError('Invalid channel')
    if args.preferred_reply_channel not in REPLY_CHANNELS:
        raise ValueError('Invalid preferred reply channel')
    if args.country is not None and args.country not in COUNTRIES:
        raise ValueError('Invalid country')
    if args.action == 'lookup' and not any([args.tracking_number, args.contact, args.source_chat_id]):
        raise ValueError('Lookup requires at least one identifier')
    if args.action == 'lookup' and args.source == 'speedaf' and not args.tracking_number:
        raise ValueError('Speedaf lookup requires --tracking-number')
    if args.action == 'submit' and not args.summary:
        raise ValueError('Submit requires --summary')


def build_required_action(args: argparse.Namespace) -> str:
    if args.required_action:
        return norm_text(args.required_action, MAX_LONG)
    case_type = CASE_TYPES[args.issue_type]
    return norm_text(
        f'Second-line staff should review and continue this {case_type} case based on the customer request and collected details.',
        MAX_LONG,
    )


def submit_payload(args: argparse.Namespace) -> dict[str, Any]:
    needs_human = parse_bool(args.needs_human)
    return {
        'action': 'submit',
        'case_type': CASE_TYPES[args.issue_type],
        'country': COUNTRIES[args.country] if args.country else '',
        'issue_summary': norm_text(args.summary or args.customer_request or 'Support case', MAX_SHORT),
        'customer_request': norm_text(args.customer_request or args.summary or 'Support case', MAX_LONG),
        'priority': PRIORITIES[args.priority],
        'status': 'Pending Human' if needs_human else 'New',
        'needs_human': needs_human,
        'customer_name': norm_text(args.customer_name, MAX_SHORT),
        'customer_contact': norm_phone(args.contact),
        'tracking_number': norm_text(args.tracking_number, MAX_SHORT),
        'channel': CHANNELS[args.channel],
        'source_chat_id': norm_text(args.source_chat_id, MAX_SHORT),
        'assigned_to': norm_text(args.assigned_to, MAX_SHORT),
        'required_action': build_required_action(args),
        'missing_fields': norm_text(args.missing_fields, MAX_LONG),
        'last_customer_message': norm_text(args.last_customer_message or args.customer_request, MAX_LONG),
        'requested_time': norm_text(args.requested_time, MAX_SHORT),
        'destination': norm_text(args.destination, MAX_SHORT),
        'preferred_reply_channel': REPLY_CHANNELS[args.preferred_reply_channel],
        'preferred_reply_contact': norm_text(args.preferred_reply_contact, MAX_SHORT),
        'language': LANGUAGES[args.language],
        'ai_summary': '',
        'attachment_paths': [path.strip() for path in args.attachments.split(',') if path.strip()] if args.attachments else [],
        'chat_history': [],
    }


def lookup_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        'tracking_number': norm_text(args.tracking_number, MAX_SHORT),
        'customer_contact': norm_phone(args.contact),
        'source_chat_id': norm_text(args.source_chat_id, MAX_SHORT),
    }


def emit(data: dict[str, Any]) -> int:
    print(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    return 0


def speedaf_mock_reply(args: argparse.Namespace) -> dict[str, Any]:
    mock_track = {
        'mailNo': args.tracking_number or 'MOCK',
        'action': str(args.mock_status),
        'actionName': 'Mock Status',
        'message': 'mock response',
        'msgEng': 'mock response',
        'msgLoc': 'mock response',
        'time': '2026-03-23 14:30',
        'timezone': 'UTC+8',
    }
    reply = build_tracking_reply(mock_track)
    return attach_proof({
        'ok': reply['ok'],
        'source': 'mock-speedaf',
        'action': 'tracking_lookup',
        'tracking_number': args.tracking_number or 'MOCK',
        'message': reply['message'],
        'status': reply.get('status', ''),
        'escalate': reply.get('escalate', False),
        'normalized': reply.get('normalized', {}),
    })


def speedaf_lookup(args: argparse.Namespace) -> dict[str, Any]:
    if args.mock_status:
        return speedaf_mock_reply(args)

    try:
        api_resp = speedaf_track_query(args.tracking_number, dry_run=args.dry_run, debug=args.debug)
    except SpeedafLookupError as exc:
        return {
            'ok': False,
            'source': 'speedaf-api',
            'action': 'tracking_lookup',
            'tracking_number': args.tracking_number,
            'error': str(exc),
            'customer_safe_reply': error_reply('temporary_failure', args.language)['customer_safe_reply'],
        }

    if not api_resp.get('ok'):
        return {
            'ok': False,
            'source': 'speedaf-api',
            'action': 'tracking_lookup',
            'tracking_number': args.tracking_number,
            'error': api_resp.get('error', 'speedaf_lookup_failed'),
            'error_code': api_resp.get('error_code', ''),
            'error_message': api_resp.get('error_message', ''),
            'layer': api_resp.get('layer', ''),
            'meta': api_resp.get('meta', {}),
            'data': api_resp.get('data', {}),
            'customer_safe_reply': error_reply('temporary_failure', args.language)['customer_safe_reply'],
        }

    if args.dry_run:
        return {
            'ok': True,
            'source': 'speedaf-api',
            'action': 'tracking_lookup',
            'tracking_number': args.tracking_number,
            'dry_run': True,
            'data': api_resp.get('data', {}),
            'meta': api_resp.get('meta', {}),
        }

    reply = build_tracking_reply(api_resp.get('data'))
    out = {
        'ok': reply['ok'],
        'source': 'speedaf-api',
        'action': 'tracking_lookup',
        'tracking_number': args.tracking_number,
        'message': reply['message'],
        'status': reply.get('status', ''),
        'escalate': reply.get('escalate', False),
        'normalized': reply.get('normalized', {}),
        'meta': api_resp.get('meta', {}),
    }
    return attach_proof(out)


def run_action(args: argparse.Namespace) -> dict[str, Any]:
    print("TRACE: support_request_bus.run_action START", flush=True)
    action = args.action
    if action == 'auto':
        action = 'submit' if args.summary else 'lookup'

    # Decouple Speedaf lookup from local Helpdesk authentication
    if action == 'lookup' and args.source == 'speedaf':
        print("TRACE: support_request_bus.run_action calling speedaf_lookup locally", flush=True)
        return speedaf_lookup(args)

    token = login_token()
    if action == 'lookup':
        print("TRACE: support_request_bus.run_action calling /bus/lookup via json_request", flush=True)
        return attach_proof(json_request('/bus/lookup', lookup_payload(args), token=token))
    if action == 'submit':
        print("TRACE: support_request_bus.run_action calling /bus/submit via json_request", flush=True)
        return attach_proof(json_request('/bus/submit', submit_payload(args), token=token))
    return attach_proof(json_request('/bus/auto', submit_payload(args), token=token))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description='Unified SPEEDAF support case bus (Helpdesk backend + optional Speedaf lookup).')
    ap.add_argument('--action', default='auto', choices=sorted(ALLOWED_ACTIONS))
    ap.add_argument('--source', default='local', choices=sorted(ALLOWED_SOURCES))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--mock-status', default='')
    ap.add_argument('--debug', action='store_true')
    ap.add_argument('--issue-type', default='other', choices=sorted(CASE_TYPES.keys()))
    ap.add_argument('--country', choices=sorted(COUNTRIES.keys()))
    ap.add_argument('--tracking-number', default='')
    ap.add_argument('--contact', default='')
    ap.add_argument('--source-chat-id', default='')
    ap.add_argument('--summary', default='')
    ap.add_argument('--case-title', default='')
    ap.add_argument('--customer-name', default='')
    ap.add_argument('--customer-request', default='')
    ap.add_argument('--destination', default='')
    ap.add_argument('--requested-time', default='')
    ap.add_argument('--priority', default='normal', choices=sorted(PRIORITIES.keys()))
    ap.add_argument('--language', default='other', choices=sorted(LANGUAGES.keys()))
    ap.add_argument('--channel', default='whatsapp', choices=sorted(CHANNELS.keys()))
    ap.add_argument('--preferred-reply-channel', default='other', choices=sorted(REPLY_CHANNELS.keys()))
    ap.add_argument('--preferred-reply-contact', default='')
    ap.add_argument('--required-action', default='')
    ap.add_argument('--last-customer-message', default='')
    ap.add_argument('--missing-fields', default='')
    ap.add_argument('--attachments', default='', help='Comma-separated list of local file paths for images/documents')
    ap.add_argument('--assigned-to', default='')
    ap.add_argument('--needs-human', default='true')
    ap.add_argument('--limit', type=int, default=3)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    try:
        validate_args(args)
        return emit(run_action(args))
    except urllib.error.HTTPError as exc:
        code = 'upstream_http_error' if exc.code >= 500 else 'request_failed'
        return emit(error_reply(code, args.language))
    except urllib.error.URLError:
        return emit(error_reply('backend_unreachable', args.language))
    except ValueError:
        return emit(error_reply('invalid_request', args.language))
    except Exception as exc:
        debug = {
            'ok': False,
            'error': 'temporary_failure',
            'customer_safe_reply': error_reply('temporary_failure', args.language)['customer_safe_reply'],
        }
        if args.debug:
            debug['detail'] = str(exc)
        return emit(debug)


if __name__ == '__main__':
    raise SystemExit(main())
