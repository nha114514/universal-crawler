"""Bounded public HTTP retrieval for page and media responses."""
import ipaddress
import re
import socket
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit

from curl_cffi import requests


class HttpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def validate_public_url(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Invalid public URL')
        try:
            literal = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise ValueError('Non-public literal address')
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0].split('%')[0]).is_global for row in addresses):
            raise ValueError('Non-public address')
    except (ValueError, OSError) as error:
        raise HttpError('UNSAFE_URL', 'URL failed public address validation') from error


def safe_get(url, headers=None, timeout=20, impersonate='chrome120', validator=None, max_body_bytes=32 * 1024 * 1024):
    validate = validator or validate_public_url
    current, request_headers = url, dict(headers or {})
    wanted = next((value for key, value in request_headers.items() if key.lower() == 'range'), '')
    match = re.fullmatch(r'bytes=(\d+)-(\d+)', wanted) if wanted else None
    if wanted and not match:
        raise HttpError('INVALID_RANGE', 'Invalid Range parameter')
    expected = (int(match[1]), int(match[2])) if match else None
    if expected and expected[1] < expected[0]:
        raise HttpError('INVALID_RANGE', 'Invalid Range interval')
    maximum = min(max_body_bytes, expected[1] - expected[0] + 1) if expected else max_body_bytes
    for hop in range(5):
        validate(current)
        response = requests.get(current, headers=request_headers, timeout=timeout, impersonate=impersonate,
                                allow_redirects=False, stream=True)
        try:
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            status = response.status_code
            if status in {301, 302, 303, 307, 308}:
                location = response_headers.get('location')
                if not location or hop == 4:
                    raise HttpError('REDIRECT_LIMIT', 'Too many media redirects or missing target address')
                target = urljoin(current, location)
                if (urlsplit(current).scheme, urlsplit(current).netloc) != (urlsplit(target).scheme, urlsplit(target).netloc):
                    request_headers = {key: value for key, value in request_headers.items() if key.lower() not in {'cookie', 'authorization'}}
                current = target
                continue
            if status >= 400:
                return SimpleNamespace(status_code=status, headers=response_headers, content=b'', text='', url=current)
            if expected:
                supplied = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response_headers.get('content-range', ''))
                if status != 206 or not supplied or (int(supplied[1]), int(supplied[2])) != expected or int(supplied[3]) <= expected[1]:
                    raise HttpError('RANGE_MISMATCH', 'Server did not return requested byte interval')
            try:
                declared = int(response_headers.get('content-length', '0'))
            except ValueError:
                declared = 0
            if declared > maximum:
                raise HttpError('SIZE_LIMIT', 'Response exceeds size limit for this request')
            chunks, size = [], 0
            for chunk in response.iter_content():
                size += len(chunk)
                if size > maximum:
                    raise HttpError('SIZE_LIMIT', 'Response exceeds size limit for this request')
                chunks.append(chunk)
            content = b''.join(chunks)
            if expected and size != maximum:
                raise HttpError('RANGE_MISMATCH', 'Chunk response insufficient bytes')
            return SimpleNamespace(status_code=status, headers=response_headers, content=content,
                                   text=content.decode('utf-8', errors='replace'), url=current)
        finally:
            response.close()
    raise HttpError('REDIRECT_LIMIT', 'Redirect not completed')
