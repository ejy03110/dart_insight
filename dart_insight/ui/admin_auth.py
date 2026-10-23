"""Process-local admin grants bound to Streamlit's server-assigned session ID.

Session state holds an opaque proof, never authority or credentials. No public
function accepts a caller-supplied identity, grant, credential hash, or role.
"""
from dataclasses import dataclass
import getpass
import hashlib
import hmac
import os
import re
import secrets
from threading import RLock
import time

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

HASH_NAME = 'DART_ADMIN_PASSWORD_HASH'
TTL = 1800
ATTEMPTS = 5
WINDOW = 60
_lock = RLock()
_grants = {}
_failures = []


@dataclass(frozen=True)
class _Grant:
    proof_digest: bytes
    credential_digest: bytes
    expires: float


def hash_password(password):
    """Provisioning helper. Return a salted hash; never retain the input."""
    if not isinstance(password, str) or len(password) < 16 or len(password) > 1024:
        raise ValueError('관리자 비밀번호는 16~1024자로 설정하세요.')
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)
    return f'pbkdf2_sha256$600000${salt.hex()}${digest.hex()}'


def _credential():
    raw = os.environ.get(HASH_NAME)
    if raw is None:
        try:
            raw = st.secrets.get(HASH_NAME)
        except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
            return None
    if not isinstance(raw, str) or len(raw) > 256:
        return None
    try:
        algorithm, count, salt, digest = raw.split('$')
        count = int(count)
        salt, digest = bytes.fromhex(salt), bytes.fromhex(digest)
        if algorithm != 'pbkdf2_sha256' or not 600000 <= count <= 1000000 or len(salt) != 16 or len(digest) != 32:
            return None
    except (TypeError, ValueError):
        return None
    return count, salt, digest, hashlib.sha256(raw.encode()).digest()


def configured():
    return _credential() is not None


def _session_id():
    ctx = get_script_run_ctx(suppress_warning=True)
    return ctx.session_id if ctx is not None else None


def _prune(now):
    for session_id in list(_grants):
        if _grants[session_id].expires <= now:
            del _grants[session_id]
    _failures[:] = [t for t in _failures if now - t < WINDOW]


def authenticate(password):
    session_id, credential = _session_id(), _credential()
    if session_id is None or credential is None:
        return False
    with _lock:
        now = time.monotonic()
        _prune(now)
        if len(_failures) >= ATTEMPTS:
            return False
        if not isinstance(password, str) or not 16 <= len(password) <= 1024:
            _failures.append(now)
            return False
        try:
            encoded = password.encode('utf-8')
        except UnicodeError:
            _failures.append(now)
            return False
        count, salt, expected, fingerprint = credential
        actual = hashlib.pbkdf2_hmac('sha256', encoded, salt, count)
        if not hmac.compare_digest(actual, expected):
            _failures.append(now)
            return False
        proof = secrets.token_urlsafe(32)
        _grants[session_id] = _Grant(hashlib.sha256(proof.encode()).digest(), fingerprint, now + TTL)
        st.session_state['admin_proof'] = proof
        return True


def is_admin():
    session_id, credential = _session_id(), _credential()
    if session_id is None:
        return False
    if credential is None:
        with _lock:
            _grants.pop(session_id, None)
        return False
    proof = st.session_state.get('admin_proof')
    if not isinstance(proof, str) or re.fullmatch(r'[A-Za-z0-9_-]{43}', proof) is None:
        return False
    with _lock:
        _prune(time.monotonic())
        grant = _grants.get(session_id)
        if grant and not hmac.compare_digest(grant.credential_digest, credential[3]):
            _grants.pop(session_id, None)
            return False
        return bool(grant and hmac.compare_digest(grant.proof_digest, hashlib.sha256(proof.encode()).digest()))


def require_admin():
    if not is_admin():
        raise PermissionError('관리자 인증이 필요합니다.')


def logout():
    session_id = _session_id()
    with _lock:
        _grants.pop(session_id, None)
    st.session_state.pop('admin_proof', None)
    st.session_state['diagnostic_open'] = False


def _submit_login():
    password = st.session_state.get('_admin_password', '')
    try:
        st.session_state['admin_login_failed'] = not authenticate(password)
    finally:
        st.session_state['_admin_password'] = ''


def render_login():
    st.title('관리자 로그인')
    if not configured():
        st.info('관리자 로그인을 사용할 수 없습니다.')
        return
    with st.form('admin_login', clear_on_submit=True):
        st.text_input('관리자 비밀번호', type='password', key='_admin_password')
        st.form_submit_button('로그인', on_click=_submit_login)
    if st.session_state.get('admin_login_failed'):
        st.error('로그인하지 못했습니다. 입력 내용을 확인하거나 잠시 후 다시 시도하세요.')


if __name__ == '__main__':
    first = getpass.getpass('새 관리자 비밀번호 (16자 이상): ')
    second = getpass.getpass('비밀번호 확인: ')
    if not hmac.compare_digest(first.encode(), second.encode()):
        raise SystemExit('비밀번호가 일치하지 않습니다.')
    print(hash_password(first))
