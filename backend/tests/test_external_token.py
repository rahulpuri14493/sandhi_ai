"""Unit tests for external job token helpers."""
import jwt
from core.external_token import create_job_token, verify_job_token, get_share_url
from core.security import SECRET_KEY, ALGORITHM


def test_create_job_token_returns_jwt_string():
    """create_job_token returns a non-empty JWT string."""
    token = create_job_token(job_id=42)
    assert isinstance(token, str)
    assert len(token) > 20
    assert token.count(".") == 2  # JWT format: header.payload.signature


def test_verify_job_token_valid():
    """verify_job_token returns True for valid token."""
    token = create_job_token(job_id=99)
    assert verify_job_token(token, 99) is True


def test_verify_job_token_wrong_job_id():
    """verify_job_token returns False when job_id does not match."""
    token = create_job_token(job_id=99)
    assert verify_job_token(token, 100) is False


def test_verify_job_token_empty():
    """verify_job_token returns False for empty token."""
    assert verify_job_token("", 1) is False


def test_verify_job_token_invalid_string():
    """verify_job_token returns False for invalid JWT."""
    assert verify_job_token("not-a-valid-jwt", 1) is False


def test_get_share_url_format():
    """get_share_url returns URL with job id and valid token."""
    url = get_share_url(job_id=5)
    assert "/api/external/jobs/5?token=" in url
    assert "token=" in url
    token_part = url.split("token=")[1]
    assert verify_job_token(token_part, 5) is True


def test_get_share_url_uses_default_base():
    """get_share_url uses default base when EXTERNAL_API_BASE_URL not set."""
    url = get_share_url(job_id=10)
    # Default is http://localhost:8000
    assert "localhost" in url or "api/external/jobs/10" in url


def test_job_token_decodes_with_pyjwt_and_expected_claims():
    """Job token should decode via PyJWT using configured algorithm and claims."""
    token = create_job_token(job_id=123)

    header = jwt.get_unverified_header(token)
    assert header.get("alg") == ALGORITHM

    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload.get("job_id") == 123
    assert payload.get("type") == "external_view"
    assert "exp" in payload
