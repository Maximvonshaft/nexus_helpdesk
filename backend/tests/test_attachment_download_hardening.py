from __future__ import annotations

from app.services.file_service import (
    ACTIVE_CONTENT_ATTACHMENT_MIME_TYPES,
    DEFAULT_ATTACHMENT_DOWNLOAD_MEDIA_TYPE,
    safe_attachment_download_media_type,
)


def test_attachment_download_downgrades_active_content_media_types():
    for media_type in ACTIVE_CONTENT_ATTACHMENT_MIME_TYPES:
        assert safe_attachment_download_media_type(media_type) == DEFAULT_ATTACHMENT_DOWNLOAD_MEDIA_TYPE


def test_attachment_download_preserves_safe_media_types():
    assert safe_attachment_download_media_type('application/pdf') == 'application/pdf'
    assert safe_attachment_download_media_type('image/png') == 'image/png'
    assert safe_attachment_download_media_type('text/plain; charset=utf-8') == 'text/plain'


def test_attachment_download_defaults_missing_media_type_to_octet_stream():
    assert safe_attachment_download_media_type(None) == DEFAULT_ATTACHMENT_DOWNLOAD_MEDIA_TYPE
    assert safe_attachment_download_media_type('') == DEFAULT_ATTACHMENT_DOWNLOAD_MEDIA_TYPE
