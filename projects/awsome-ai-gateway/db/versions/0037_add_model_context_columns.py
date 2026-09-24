# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""model_aliases에 context_window / max_output_tokens 컬럼 추가

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-21

모델 레지스트리에 스펙 정보가 없어 콘솔 상세가 0으로 표시됐다. LiteLLM model
catalog 가격 싱크와 같은 소스에서 max_input_tokens/max_output_tokens 를 가져와
채운다. NULL 허용 — 싱크에 매칭되지 않는 커스텀 모델(OPENMODEL/vLLM)은 비어 있을
수 있다.
"""

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE model.model_aliases ADD COLUMN IF NOT EXISTS context_window INTEGER")
    op.execute("ALTER TABLE model.model_aliases ADD COLUMN IF NOT EXISTS max_output_tokens INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE model.model_aliases DROP COLUMN IF EXISTS context_window")
    op.execute("ALTER TABLE model.model_aliases DROP COLUMN IF EXISTS max_output_tokens")
