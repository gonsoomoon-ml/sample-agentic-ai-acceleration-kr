# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""admin-ui 세션 JWT(RS256) 서명용 RSA 키쌍 생성.

사용법 (수동):
    python scripts/generate_admin_jwt_keypair.py

사용법 (자동화 — deployment/scripts/setup-admin-ui-login.sh 가 사용):
    python scripts/generate_admin_jwt_keypair.py --private-out /tmp/priv.pem --public-out /tmp/pub.pem --quiet

출력된 두 PEM 을 각각:
  - PRIVATE KEY → admin-api 의 ``ADMIN_UI_JWT_PRIVATE_KEY_PEM`` 환경변수(비밀값, Secrets
    Manager 등에 저장)에 설정.
  - PUBLIC KEY  → ``db/init/03_seed_data.sql`` 의
    ``auth.admin_jwt_configs.public_key_pem`` (id=ADMIN_UI_JWT_CONFIG_ID, 기본
    ``00000000-0000-4000-a000-000000000030``) 값을 이 값으로 UPDATE.
    (이미 배포된 환경이면 seed 재실행 대신 직접 UPDATE 문 사용)

두 값은 반드시 한 쌍으로 교체해야 한다 — 개인키만 바꾸면 검증이 깨진다.
"""
from __future__ import annotations

import argparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_keypair() -> tuple[str, str]:
    """Returns (private_pem, public_pem)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return private_pem, public_pem


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-out", help="개인키 PEM 을 이 경로에 저장 (자동화용)")
    parser.add_argument("--public-out", help="공개키 PEM 을 이 경로에 저장 (자동화용)")
    parser.add_argument(
        "--quiet", action="store_true", help="파일로 저장할 때 사람이 읽는 안내 텍스트 생략"
    )
    args = parser.parse_args()

    private_pem, public_pem = generate_keypair()

    if args.private_out:
        with open(args.private_out, "w", encoding="utf-8") as f:
            f.write(private_pem)
    if args.public_out:
        with open(args.public_out, "w", encoding="utf-8") as f:
            f.write(public_pem)

    if args.quiet and args.private_out and args.public_out:
        return

    print("=" * 78)
    print("PRIVATE KEY — set as admin-api ADMIN_UI_JWT_PRIVATE_KEY_PEM (secret)")
    print("=" * 78)
    print(private_pem)
    print("=" * 78)
    print("PUBLIC KEY — UPDATE auth.admin_jwt_configs.public_key_pem")
    print("=" * 78)
    print(public_pem)
    print(
        "UPDATE auth.admin_jwt_configs SET public_key_pem = '<PUBLIC KEY ABOVE>' "
        "WHERE id = '00000000-0000-4000-a000-000000000030';"
    )


if __name__ == "__main__":
    main()
